"""Haeyang mobile-web BizMOB adapter. Public wire evidence: haeyang-protocol.md."""

from __future__ import annotations

import asyncio
import hashlib
import json
from calendar import monthrange
from datetime import datetime
from decimal import ROUND_HALF_UP
from zoneinfo import ZoneInfo

import aiohttp

from .model import Bill, GasError, MeterWindow, decimal
from .portal import AuthenticationError, ConnectionError, Contract, opaque
from .provider_transport import day, month, number, required, text
from .submission_transport import SubmissionNotSent, SubmissionRejected, SubmissionUncertain

BASE = "https://m.hyenergy.co.kr/bizmob/"
TR_CODES = frozenset({"LOGIN", "MYPAGE3", "BILL001", "BILL002", "SELF100", "SELF101"})


def today():
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def envelope(trcode, body):
    return {
        "header": {
            "result": True,
            "error_code": "",
            "error_text": "",
            "info_text": "WEB",
            "message_version": "",
            "login_session_id": "",
            "trcode": trcode,
        },
        "body": body,
    }


def decode(payload):
    """getDecAES is an identity operation for the public WEB transport."""
    if not isinstance(payload, dict) or not isinstance(payload.get("header"), dict):
        raise GasError("provider_schema_changed")
    header = payload["header"]
    if header.get("result") is not True:
        code = str(header.get("error_code", ""))
        if code.startswith("LOGIN01") or code == "ERR000":
            raise AuthenticationError(
                "invalid_auth" if code.startswith("LOGIN01") else "reauth_required"
            )
        raise GasError("provider_request_rejected")
    body = payload.get("body")
    if not isinstance(body, dict):
        raise GasError("provider_schema_changed")
    return body


def rows(body, key="IT_TAB"):
    value = body.get(key)
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise GasError("provider_schema_changed")
    return value


async def request(session, trcode, body):
    """POST form, bounded response, no redirects and never retry a transaction."""
    if trcode not in TR_CODES:
        raise GasError("provider_endpoint_not_allowed")
    try:
        async with session.post(
            BASE + trcode + ".json",
            data={"message": json.dumps(envelope(trcode, body), ensure_ascii=False)},
            headers={
                "Origin": "https://m.hyenergy.co.kr",
                "Referer": BASE + "contents/MAI/jsp/MAI0100.jsp",
            },
            allow_redirects=False,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as response:
            if response.status in (401, 403):
                raise AuthenticationError("reauth_required")
            if response.status != 200:
                raise GasError("provider_request_failed")
            data = bytearray()
            async for chunk in response.content.iter_chunked(65536):
                data.extend(chunk)
                if len(data) > 4_000_000:
                    raise GasError("provider_response_too_large")
            try:
                return decode(json.loads(data))
            except (ValueError, UnicodeError):
                raise GasError("provider_schema_changed") from None
    except (aiohttp.ClientError, asyncio.TimeoutError):
        raise ConnectionError("cannot_connect") from None


class HaeyangClient:
    def __init__(self, session, data, provider):
        if provider.id != "haeyang" or provider.family != "haeyang":
            raise GasError("provider_mismatch")
        self.session, self.data, self.provider = session, data, provider
        self.history_errors = {}
        self.login_body = None
        self.lock = asyncio.Lock()

    async def login(self):
        async with self.lock:
            if self.login_body is not None:
                return self.login_body
            username, password = required(self.data, "username"), required(self.data, "password")
            legacy = envelope(
                "LOGIN01",
                {
                    "userId": username,
                    "userPwdAsMD5": hashlib.md5(
                        password.encode(), usedforsecurity=False
                    ).hexdigest(),
                    "userPwdAsSHA2": hashlib.sha256(password.encode()).hexdigest(),
                },
            )
            response = await request(
                self.session,
                "LOGIN",
                {
                    "user_id": "",
                    "password": "",
                    "os_type": "mobileweb",
                    "legacy_message": legacy,
                    "legacy_trcode": "LOGIN01",
                    "emulator_flag": True,
                    "device_id": "",
                    "app_key": "HYCGANP0",
                    "phone_number": "",
                    "manual_phone_number": False,
                },
            )
            self.login_body = decode(response.get("legacy_message"))
            rows(self.login_body, "payerList")
            return self.login_body

    async def call(self, code, body):
        await self.login()
        try:
            return await request(self.session, code, body)
        except AuthenticationError:
            self.login_body = None
            raise

    def account(self, contract):
        if (
            contract.key != opaque("haeyang:" + contract.cano)
            or contract.private.get("payer") != contract.cano
        ):
            raise GasError("provider_mismatch")
        return contract.private

    async def contracts(self):
        payload = await self.login()
        result = []
        seen = set()
        for row in rows(payload, "payerList"):
            payer = required(row, "PAYERNO")
            if payer in seen:
                raise GasError("contract_schema_changed")
            seen.add(payer)
            name = text(row, "PAYERNM") or ""
            query = {
                "I_CUSTCG": "2" if row.get("isBusinessCust") is True else "1",
                "I_PAYERNM": name,
                "I_PAYERNO": payer,
                "PAYERNM": name,
                "PAYERNO": payer,
            }
            details = rows(await self.call("MYPAGE3", {"payerList": [query]}), "payerList")
            if len(details) != 1 or text(details[0], "PAYERNO") not in (None, payer):
                raise GasError("contract_schema_changed")
            detail = details[0]
            if text(detail, "RETCODE") not in (None, "00"):
                raise GasError("contract_schema_changed")
            install = text(detail, "INSTALLNO") or text(row, "INSTALLNO")
            result.append(
                Contract(
                    opaque("haeyang:" + payer),
                    self.data["username"],
                    payer,
                    text(detail, "ADDRESS") or "해양에너지 계약",
                    {"payer": payer, "install": install},
                )
            )
        return result

    async def bills(self, contract, cached=None, progress=None):
        self.account(contract)
        date_ = today()
        payload = await self.call(
            "BILL001",
            {
                "actualBillingCheck": True,
                "checkPeriod": "MONTH",
                "FROMYM": f"{date_.year - 2}01",
                "TOYM": date_.strftime("%Y%m"),
                "PAYERNO": contract.cano,
            },
        )
        result = dict(cached or {})
        errors = []
        entries = rows(payload)
        for completed, row in enumerate(entries, start=1):
            key = month(required(row, "YEARMONTH"))
            amount = number(row, "NOTICE_AMT")
            # Public FEE_COM0100.toCommaNumber multiplies SAP currency amounts by 100.
            amount = (
                str((decimal(amount) * 100).quantize(decimal("1"), rounding=ROUND_HALF_UP))
                if amount is not None
                else None
            )
            bill = Bill(key, amount, [], base_charge="0", reported_usage=number(row, "CONSUME_QTY"))
            notice = text(row, "NOTICENO")
            if notice:
                try:
                    details = rows(
                        await self.call(
                            "BILL002", {"NOTICENO": notice, "PAYERNO": contract.cano, "YYYYMM": key}
                        )
                    )
                    if len(details) != 1:
                        raise GasError("provider_schema_changed")
                    detail = details[0]
                    start, end = day(detail, "USE_PERIOD_FROM"), day(detail, "USE_PERIOD_TO")
                    if start and end and start <= end:
                        bill.period_start, bill.period_end = start, end
                    bill.closing_reading = number(detail, "CURR_INDCT")
                    meter = text(detail, "INSTALLNO")
                    bill.meter_id = opaque(f"haeyang:{contract.cano}:{meter}") if meter else None
                    bill.reported_usage = number(detail, "CONSUME_QTY") or bill.reported_usage
                except AuthenticationError:
                    raise
                except GasError as error:
                    errors.append(str(error))
            result[key] = bill.dump()
            if progress:
                progress(result, completed, len(entries))
        self.history_errors[contract.key] = sorted(set(errors))
        return result

    async def meter(self, contract):
        account = self.account(contract)
        if not account.get("install"):
            raise GasError("meter_selection_required")
        response = await self.call(
            "SELF100", {"INSTALLNO": account["install"], "PAYERNO": contract.cano}
        )
        data = rows(response)
        if len(data) != 1:
            raise GasError("meter_selection_required")
        row = data[0]
        if text(row, "INSTALLNO") not in (None, account["install"]):
            raise GasError("meter_schema_changed")
        code = required(response, "RTNCD")
        if code not in ("00", "01"):
            raise GasError("meter_schema_changed")
        previous_date = day(row, "METERDATE")
        date_ = today()
        if previous_date:
            prior = datetime.fromisoformat(previous_date).date()
            year, month_ = (
                (prior.year + 1, 1) if prior.month == 12 else (prior.year, prior.month + 1)
            )
            date_ = prior.replace(year=year, month=month_, day=1)
        days = {
            "A": (1, 5),
            "B": (6, 10),
            "C": (11, 15),
            "S": (
                monthrange(date_.year, date_.month)[1] - 1,
                monthrange(date_.year, date_.month)[1],
            ),
        }
        payment = required(row, "PAYMENTDATE")
        if payment not in days:
            raise GasError("meter_schema_changed")
        start, end = (date_.replace(day=part).isoformat() for part in days[payment])
        previous = number(row, "PREV_INDCT")
        if previous is None:
            raise GasError("meter_schema_changed")
        accepted_date = day(row, "METERDATE_CM")
        accepted = number(row, "NREVB_INDCT")
        private = {"account_key": contract.key, "order": required(row, "MTORDERNO")}
        private["cycle_id"] = opaque(
            f"haeyang:{contract.key}:{account['install']}:{private['order']}"
        )
        submitted = accepted if accepted_date and start <= accepted_date <= end else None
        if accepted_date and start <= accepted_date <= end and accepted is None:
            private["submission_blocked"] = "submission_state_unknown"
        elif submitted is None and accepted not in (None, "0"):
            private["submission_blocked"] = "portal_reading_present"
        return MeterWindow(
            start,
            end,
            previous,
            opaque(f"haeyang:{contract.cano}:{account['install']}"),
            eligible=code == "00",
            submitted=submitted,
            private=private,
        )

    async def submit(self, contract, expected, value, *, now):
        if now.tzinfo is None:
            raise SubmissionNotSent("invalid_submission_time")
        numeric = decimal(value)
        fresh = await self.meter(contract)
        if (
            expected.private.get("account_key") != contract.key
            or fresh.cycle != expected.cycle
            or fresh.previous != expected.previous
            or fresh.private["order"] != expected.private.get("order")
            or not fresh.is_open(now.date())
            or fresh.submitted is not None
            or fresh.private.get("submission_blocked")
            or numeric != int(numeric)
            or numeric < decimal(fresh.previous)
            or numeric > 99999999
        ):
            raise SubmissionNotSent("stale_proposal")
        try:
            await self.call(
                "SELF101",
                {
                    "CURR_INDCT": str(int(numeric)),
                    "MTORDERNO": fresh.private["order"],
                    "IFFLAG": "W",
                },
            )
        except GasError as error:
            if str(error) == "provider_request_rejected":
                raise SubmissionRejected("submission_rejected") from None
            raise SubmissionUncertain("submission_uncertain") from None
        # Coordinator performs same-contract readback; header success is not a receipt.
