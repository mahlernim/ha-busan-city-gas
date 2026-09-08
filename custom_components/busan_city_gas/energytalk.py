"""EnergyTalk session adapter from its public 20260828 frontend wire contract.

The official Kakao redirect belongs to EnergyTalk. Users import an existing
address-selected session; this adapter never exchanges codes or changes addresses.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from urllib.parse import quote
from zoneinfo import ZoneInfo

import aiohttp

from .model import Bill, GasError, MeterWindow, decimal
from .portal import AuthenticationError, ConnectionError, Contract, opaque
from .provider_transport import month, number, request, required, text
from .submission_transport import SubmissionNotSent, SubmissionRejected, SubmissionUncertain

BASE = "https://energytalk.ai"
TENANTS = frozenset(
    (
        "cncity",
        "kne",
        "ktrm",
        "miraense",
        "srb",
        "gse",
        "cwjgas",
        "ccbgas",
        "cydgas",
        "cdhgas",
        "cscgas",
    )
)
READS = frozenset(
    (
        "/gas/api/user/info",
        "/gas/api/address/list/registered",
        "/gas/api/pay/usage",
        "/gas/api/self-meter",
    )
)
CHECK = "/gas/api/self-meter/check"
WRITE = "/gas/api/self-meter"
KOREA = ZoneInfo("Asia/Seoul")


def envelope(payload):
    if not isinstance(payload, dict):
        raise GasError("provider_schema_changed")
    code = payload.get("responseCode")
    if code in ("no-token", "expired-token", "invalid-token"):
        raise AuthenticationError("reauth_required")
    if code != "ok":
        raise GasError("provider_request_rejected")
    return payload


def volume(row, key):
    """Only recognized volume units; never confuse MJ and cubic metres."""
    value = text(row, key)
    if value is None:
        return None
    value = re.sub(r"\s*(?:m³|m3|㎥)\s*$", "", value)
    return number({key: value}, key)


def listing(payload):
    entries = payload.get("list")
    if (
        not isinstance(entries, list)
        or any(not isinstance(x, dict) for x in entries)
        or len(entries) > 600
    ):
        raise GasError("provider_schema_changed")
    return entries


class EnergyTalkClient:
    def __init__(self, session, data, provider):
        self.session, self.data, self.provider = session, data, provider
        self.history_errors, self.readings = {}, {}
        self.auth_expired = False
        if provider.path not in TENANTS:
            raise GasError("unsupported_provider")
        token = data.get("energytalk_token", "")
        if (
            not isinstance(token, str)
            or not token
            or len(token) > 16384
            or any(ord(c) <= 32 or ord(c) >= 127 for c in token)
        ):
            raise AuthenticationError("invalid_auth")
        self.headers = {
            "Authorization": "Bearer " + token,
            "Accept": "application/json",
            "Origin": BASE,
            "Referer": BASE + "/gas",
        }

    async def call(self, method, path, body=None):
        if not (method == "GET" and path in READS or method == "POST" and path == CHECK):
            raise GasError("unsupported_operation")
        if self.auth_expired:
            raise AuthenticationError("reauth_required")
        try:
            return envelope(
                await request(
                    self.session,
                    "POST",
                    BASE + "/api/fetch",
                    headers=self.headers,
                    body={"method": method, "url": path, "body": body or {}},
                    stage="meter" if "meter" in path else "query",
                )
            )
        except AuthenticationError:
            # Imported sessions cannot refresh themselves. A new client created
            # after reauthentication is the only way to resume provider calls.
            self.auth_expired = True
            raise

    async def identity(self):
        info = await self.call("GET", "/gas/api/user/info")
        if required(info, "clientId") != self.provider.path:
            raise GasError("wrong_account")
        return info

    async def contracts(self):
        info = await self.identity()
        # Queries are scoped by the imported token's currently selected address.
        # Listing every registered address would falsely suggest per-call routing.
        address = text(info, "address")
        if not address:
            raise GasError("energytalk_address_required")
        entries = listing(await self.call("GET", "/gas/api/address/list/registered"))
        matches = [
            row
            for row in entries
            if text(row, "address") == address and text(row, "typeVal") != "3"
        ]
        if len(matches) != 1:
            raise GasError("energytalk_address_required")
        customer = required(matches[0], "custNo")
        key = opaque(f"energytalk:{self.provider.path}:{customer}")
        return [
            Contract(
                key,
                opaque(f"energytalk:{self.provider.path}:{customer}"),
                customer,
                f"계약 {customer[-4:]}",
                {"tenant": self.provider.path, "custNo": customer, "address": address},
            )
        ]

    async def check_account(self, contract):
        account = contract.private
        if (
            account.get("tenant") != self.provider.path
            or contract.key
            != opaque(f"energytalk:{self.provider.path}:{required(account, 'custNo')}")
            or contract.cano != account["custNo"]
        ):
            raise GasError("wrong_account")
        info = await self.identity()
        if required(info, "address") != required(account, "address"):
            raise GasError("wrong_account")
        # Some tenants expose the number as well; never ignore a contradiction.
        if text(info, "custNo") and text(info, "custNo") != account["custNo"]:
            raise GasError("wrong_account")

    async def bills(self, contract, cached, progress=None):
        await self.check_account(contract)
        entries = listing(await self.call("GET", "/gas/api/pay/usage"))
        result, seen, errors = dict(cached), set(), []
        for row in entries:
            key = month(required(row, "dateVal"))
            if key in seen:
                raise GasError("ambiguous_bill")
            seen.add(key)
            try:
                usage = volume(row, "usageVal")
            except GasError:
                usage = None
                errors.append("provider_usage_unit_unknown")
            result[key] = Bill(
                key, number(row, "amount"), [], base_charge=None, reported_usage=usage
            ).dump()
        self.history_errors[contract.key] = sorted(set(errors))
        if progress:
            progress(result, len(seen), len(seen))
        return result

    async def meter(self, contract):
        await self.check_account(contract)
        row = await self.call("GET", WRITE)
        if row.get("checkYn") not in ("Y", "N"):
            raise GasError("meter_schema_changed")
        serial = text(row, "meterNumber")
        previous, submitted = volume(row, "prevGuideline"), volume(row, "recentGuideLine")
        # checkYn is current permission, not a documented calendar. Represent a
        # one-day permission snapshot; never invent a future window or due date.
        today = datetime.now(KOREA).date().isoformat()
        # Permission refreshes daily, but duplicate-write protection must not.
        # A changed bill month or disappearing recentGuideLine does not prove
        # rollover. Only a new official baseline or meter changes this identity.
        # With zero usage across months this remains locked conservatively.
        baseline = format(decimal(previous).normalize(), "f") if previous is not None else "unknown"
        cycle_id = opaque(f"energytalk:{contract.key}:{serial or 'unknown'}:{baseline}")
        return MeterWindow(
            today,
            today,
            previous or "",
            opaque(f"energytalk:{self.provider.path}:{serial}") if serial else "",
            eligible=bool(row["checkYn"] == "Y" and serial and previous is not None),
            submitted=submitted,
            private={
                "account_key": contract.key,
                "dynamic_window": True,
                "cycle_id": cycle_id,
                "submission_blocked": submitted is not None,
            },
        )

    async def submit(self, contract, expected, value, *, now):
        if now.tzinfo is None:
            raise SubmissionNotSent("invalid_submission_time")
        try:
            numeric = decimal(value)
            fresh = await self.meter(contract)
            if (
                numeric != int(numeric)
                or numeric > 99999999
                or expected.private.get("account_key") != contract.key
                or fresh.cycle != expected.cycle
                or fresh.previous != expected.previous
                or not fresh.meter
                or not fresh.is_open(now.astimezone(KOREA).date())
                or fresh.submitted is not None
                or numeric < decimal(fresh.previous)
            ):
                raise GasError("stale_proposal")
            checked = await self.call("POST", CHECK, {"guideline": str(int(numeric))})
            if checked.get("addableYn") != "Y":
                raise GasError("provider_value_not_allowed")
        except GasError as error:
            raise SubmissionNotSent(str(error)) from None
        # Exactly one mutation. An acknowledgement alone is not a receipt;
        # SubmissionManager re-reads current meter state and compares values.
        try:
            response = await self.post_reading(str(int(numeric)))
        except GasError:
            raise SubmissionUncertain("submission_uncertain") from None
        if isinstance(response, dict) and response.get("responseCode") == "fail":
            raise SubmissionRejected("submission_rejected")
        envelope(response)

    async def post_reading(self, value):
        if self.auth_expired:
            raise AuthenticationError("reauth_required")
        form = aiohttp.FormData(default_to_multipart=True)
        form.add_field("guideline", value)
        headers = {
            **self.headers,
            "X-Backend-Method": "POST",
            "X-Backend-Url": quote(WRITE, safe=""),
        }
        try:
            async with self.session.request(
                "POST",
                BASE + "/api/formdata",
                headers=headers,
                data=form,
                allow_redirects=False,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                if response.status in (401, 403):
                    self.auth_expired = True
                    raise AuthenticationError("reauth_required")
                if not 200 <= response.status < 300:
                    raise GasError("provider_meter_http_error")
                data = bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    data.extend(chunk)
                    if len(data) > 4_000_000:
                        raise GasError("provider_response_too_large")
                try:
                    payload = json.loads(data)
                except (ValueError, UnicodeError):
                    raise GasError("provider_schema_changed") from None
                if isinstance(payload, dict) and payload.get("responseCode") in (
                    "no-token",
                    "expired-token",
                    "invalid-token",
                ):
                    self.auth_expired = True
                    raise AuthenticationError("reauth_required")
                return payload
        except (aiohttp.ClientError, asyncio.TimeoutError):
            raise ConnectionError("provider_meter_connection_failed") from None
