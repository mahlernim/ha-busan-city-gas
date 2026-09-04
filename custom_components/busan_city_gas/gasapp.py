"""Gasapp async adapter, adapted from the MIT gas-self-meter-ai protocol client.

Copyright (c) 2026 mahlernim. See LICENSE and NOTICE.md.
No account mutation occurs during reads or session expiry handling.
"""

from __future__ import annotations

import re
import uuid
from datetime import date

from bs4 import BeautifulSoup

from .model import Bill, GasError, MeterWindow, decimal
from .portal import AuthenticationError, Contract, opaque
from .provider_transport import day, flag, month, number, request, required, rows, text, unwrap
from .submission_transport import SubmissionNotSent, SubmissionRejected, SubmissionUncertain

BASE = "https://app.gasapp.co.kr/api/"
ENDPOINTS = {
    ("GET", "documents/search/0"),
    ("POST", "extern/auth/nice/sms/request"),
    ("POST", "extern/auth/nice/sms/confirm"),
    ("POST", "members"),
    ("GET", "contracts"),
    ("GET", "home"),
    ("GET", "meters"),
    ("GET", "bills/summary"),
    ("GET", "indications"),
    ("GET", "indications/history"),
    ("POST", "indications/register"),
    ("PUT", "indications/channel"),
    ("POST", "relay/indications/input"),
}


class GasappClient:
    def __init__(self, session, data, provider):
        self.session, self.data, self.provider = session, data, provider
        self.device_id = data.get("gasapp_device_id") or str(uuid.uuid4())
        self.history_errors = {}

    async def call(self, method, path, contract=None, *, params=None, body=None):
        if (method, path) not in ENDPOINTS:
            raise GasError("unsupported_operation")
        company = "0" if path == "contracts" else "null"
        if contract:
            company = self.account(contract)["company"]
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "ko-KR,ko;q=0.9",
            "Origin": "https://app.gasapp.co.kr",
            "Referer": "https://app.gasapp.co.kr/",
            "X-VERSION": "11.5.1505",
            "X-WEBVERSION": "6.10.548",
            "X-PLATFORM": "android",
            "User-Agent": "WunderFlo Appstore/11.5.1505",
            "X-TOKEN": self.data.get("gasapp_token", ""),
            "X-MEMBER": self.data.get("gasapp_member", ""),
            "X-COMPANY": company,
            "X-ADID": self.device_id,
            "X-TID": "",
        }
        stage = "bills" if "bill" in path else "meter" if "indication" in path else "connect"
        return await request(
            self.session,
            method,
            BASE + path,
            headers=headers,
            params=params,
            body=body,
            stage=stage,
        )

    def account(self, contract):
        account = contract.private
        company = required(account, "company")
        customer, number_ = text(account, "customerNum"), text(account, "useContractNum")
        expected = opaque(f"gasapp:{company}:{customer or ''}:{number_ or ''}")
        if (
            company not in self.provider.company_codes
            or not (customer or number_)
            or expected != contract.key
        ):
            raise GasError("wrong_account")
        return account

    def params(self, contract):
        account = self.account(contract)
        # Omit absent identifiers rather than sending an invented empty identifier.
        return {k: account[k] for k in ("customerNum", "useContractNum") if account.get(k)}

    async def terms(self, carrier):
        name = {"1": "SKT", "2": "KT", "3": "LGU"}.get(carrier)
        if not name:
            raise GasError("invalid_identity")
        categories = [
            f"본인인증 약관 {name}",
            "최초 회원 가입 약관",
            "최초 회원 가입 약관-도시가스 사 정보 조회",
        ]
        result = []
        for category in categories:
            payload = await self.call("GET", "documents/search/0", params={"category": category})
            parts = []
            for row in rows(payload, "documents"):
                if not flag(row.get("necessary", True)):
                    continue
                content = text(row, "content", "contents", "body")
                if content:
                    parts.append(
                        (text(row, "title") or "")
                        + "\n"
                        + BeautifulSoup(content, "html.parser").get_text(" ", strip=True)
                    )
            if not parts:
                raise GasError("terms_unavailable")
            result.append({"category": category, "text": "\n\n".join(parts)})
        return result

    @staticmethod
    def validate_identity(identity):
        name, phone, birthday, gender, carrier = [
            identity.get(k, "") for k in ("name", "phone", "birthday", "gender", "carrier")
        ]
        if (
            not 2 <= len(name.strip()) <= 80
            or not re.fullmatch(r"01[016789][0-9]{7,8}", phone)
            or not re.fullmatch(r"[0-9]{6}", birthday)
            or gender not in ("1", "2", "3", "4")
            or carrier not in ("1", "2", "3")
        ):
            raise GasError("invalid_identity")
        birth = ("19" if gender in ("1", "2") else "20") + birthday
        if day({"birth": birth}, "birth") > date.today().isoformat():
            raise GasError("invalid_identity")
        return birth

    async def request_sms(self, identity, terms, consent):
        self.validate_identity(identity)
        expected = "본인인증 약관 " + {"1": "SKT", "2": "KT", "3": "LGU"}[identity["carrier"]]
        if (
            not consent
            or len(terms) != 3
            or terms[0]["category"] != expected
            or any(not t["text"] for t in terms)
        ):
            raise GasError("consent_required")
        row = unwrap(
            await self.call(
                "POST",
                "extern/auth/nice/sms/request",
                body={
                    "mobileCo": identity["carrier"],
                    "mobileNo": identity["phone"],
                    "birthday": identity["birthday"],
                    "gender": identity["gender"],
                    "name": identity["name"].strip(),
                },
            )
        )
        return {k: required(row, k) for k in ("requestNo", "responseUniqId")}

    async def confirm_sms(self, identity, challenge, otp):
        birth = self.validate_identity(identity)
        if not re.fullmatch(r"[0-9]{6}", otp):
            raise GasError("invalid_otp")
        verified = unwrap(
            await self.call("POST", "extern/auth/nice/sms/confirm", body={**challenge, "otp": otp})
        )
        member = unwrap(
            await self.call(
                "POST",
                "members",
                body={
                    "name": identity["name"].strip(),
                    "birthDate": birth,
                    "handphone": identity["phone"],
                    "gender": "F" if identity["gender"] in ("2", "4") else "M",
                    "ci": required(verified, "ci"),
                    "di": required(verified, "di"),
                    "marketingAcceptance": "N",
                    "nation": "N",
                    "mid": None,
                    "adid": self.device_id,
                },
            )
        )
        return {
            "gasapp_token": required(member, "token"),
            "gasapp_member": required(member, "member"),
            "gasapp_device_id": self.device_id,
        }

    async def contracts(self):
        result = {}
        for row in rows(await self.call("GET", "contracts"), "contracts"):
            company = required(row, "company")
            if company not in self.provider.company_codes:
                continue
            customer, number_ = text(row, "customerNum") or "", text(row, "useContractNum") or ""
            if not (customer or number_):
                raise GasError("contract_schema_changed")
            key = opaque(f"gasapp:{company}:{customer}:{number_}")
            account = {
                "company": company,
                "customerNum": customer,
                "useContractNum": number_,
                "amiYn": text(row, "amiYn") or "N",
            }
            result[key] = Contract(
                key,
                required(self.data, "gasapp_member"),
                number_ or customer,
                text(row, "alias", "label") or f"계약 {(number_ or customer)[-4:]} · {company}",
                account,
            )
        return list(result.values())

    async def bills(self, contract, cached, progress=None):
        payload = await self.call(
            "GET",
            "bills/summary",
            contract,
            params={**self.params(contract), "onlyUnpay": "N", "f": "annual"},
        )
        result = dict(cached)
        seen = set()
        for row in rows(payload, "history"):
            key = month(required(row, "requestYm"))
            if key in seen:
                raise GasError("ambiguous_bill")
            seen.add(key)
            start, end = day(row, "useStartDate"), day(row, "useEndDate")
            if start and end and start > end:
                raise GasError("provider_date_invalid")
            bill = Bill(
                key,
                number(row, "chargeAmtQty", "chargeAmt"),
                [],
                base_charge=None,
                reported_usage=number(row, "useQty", "usageQty"),
                period_start=start,
                period_end=end,
            )
            result[key] = bill.dump()
        # Optional reading endpoints must not discard successfully retrieved bills.
        readings, errors = [], []
        target = None
        try:
            target = await self.meter(contract)
            if target.private["registered"]:
                readings.extend(await self.history(contract))
        except AuthenticationError:
            raise
        except GasError:
            errors.append("meter_history_unavailable")
        try:
            home = unwrap(
                await self.call(
                    "GET",
                    "home",
                    contract,
                    params={
                        **self.params(contract),
                        "amiYn": self.account(contract).get("amiYn", "N"),
                    },
                )
            )
            if not isinstance(home, dict) or not isinstance(home.get("cards", {}), dict):
                raise GasError("provider_schema_changed")
            card = home.get("cards", {}).get("indication")
            if isinstance(card, dict):
                readings.extend(self.parse_readings(rows(card.get("history"), "history"), contract))
        except AuthenticationError:
            raise
        except GasError:
            errors.append("home_history_unavailable")
        self.readings = getattr(self, "readings", {})
        self.readings[contract.key] = sorted(
            {(r["date"], r["value"], r["meter"]): r for r in readings}.values(),
            key=lambda r: r["date"],
        )
        self.history_errors[contract.key] = errors
        if progress:
            progress(result, len(seen), len(seen))
        return result

    def parse_readings(self, entries, contract):
        result = []
        for row in entries:
            at = day(row, "gmtrJobYmd", "jobYmd", "readingDate", "inputDate")
            value = number(
                row, "indiCompensThisMonthVc", "thisMonthIndicator", "thisMonthIndicatorCustomer"
            )
            meter = text(row, "meterIdNum")
            if at and value is not None and meter:
                result.append(
                    {
                        "date": at,
                        "value": value,
                        "meter": opaque(f"gasapp:{self.account(contract)['company']}:{meter}"),
                    }
                )
        return result

    async def history(self, contract):
        result, cursors, cursor = {}, set(), None
        for _ in range(60):
            params = {**self.params(contract), "limit": "6"}
            if cursor:
                params["lastId"] = cursor
            page = rows(
                await self.call("GET", "indications/history", contract, params=params), "history"
            )
            if len(page) > 6:
                raise GasError("history_schema_changed")
            for reading in self.parse_readings(page[:5] if len(page) == 6 else page, contract):
                result[(reading["date"], reading["value"], reading["meter"])] = reading
            if len(page) < 6:
                return sorted(result.values(), key=lambda r: r["date"])
            cursor = required(page[-1], "id")
            if cursor in cursors:
                raise GasError("history_cursor_repeated")
            cursors.add(cursor)
        raise GasError("history_limit_exceeded")

    async def meter(self, contract):
        account = self.account(contract)
        row = unwrap(await self.call("GET", "indications", contract, params=self.params(contract)))
        registered = row is not None
        if row is None:
            row = {}
        elif not isinstance(row, dict) or not any(
            k in row for k in ("selfInputAvailable", "periodStart", "meterIdNum")
        ):
            raise GasError("meter_schema_changed")
        for key in ("company", "customerNum", "useContractNum"):
            if text(row, key) and text(row, key) != account.get(key):
                raise GasError("wrong_account")
        start, end = day(row, "periodStart"), day(row, "periodEnd")
        if start and end and start > end:
            raise GasError("provider_date_invalid")
        raw_meter = text(row, "meterIdNum")
        if not raw_meter:
            meters = rows(
                await self.call("GET", "meters", contract, params=self.params(contract)), "meters"
            )
            serials = {text(m, "meterIdNum") for m in meters} - {None}
            raw_meter = next(iter(serials)) if len(serials) == 1 else None
        previous = number(row, "lastMonthIndicatorQty")
        submitted = number(row, "thisMonthIndicatorCustomer", "thisMonthIndicator")
        channel = flag(row.get("needChangeRegisteredChannel"))
        change = row.get("meterChange") or {}
        if not isinstance(change, dict):
            raise GasError("meter_schema_changed")
        changed = flag(change.get("changeYn"))
        digits = number(row, "mtrDigitCnt")
        if digits is not None and (
            decimal(digits) != int(decimal(digits)) or not 1 <= int(decimal(digits)) <= 20
        ):
            raise GasError("meter_schema_changed")
        return MeterWindow(
            start or "",
            end or "",
            previous or "",
            opaque(f"gasapp:{account['company']}:{raw_meter}") if raw_meter else "",
            eligible=bool(
                start
                and end
                and raw_meter
                and previous is not None
                and registered
                and not channel
                and flag(row.get("selfInputAvailable"))
            ),
            submitted=submitted,
            private={
                "registered": registered,
                "needs_channel_change": channel,
                "meter_changed": changed,
                "digits": int(decimal(digits)) if digits else None,
                "account_key": contract.key,
                "submission_blocked": flag(row.get("inputYn"))
                or flag(row.get("selfInputYn"))
                or submitted is not None,
            },
        )

    async def prepare_service(self, contract, action, consent):
        if not consent:
            raise GasError("consent_required")
        self.account(contract)
        if action == "register":
            await self.call("POST", "indications/register", contract, body=self.params(contract))
        elif action == "channel":
            number_ = required(self.account(contract), "useContractNum")
            await self.call(
                "PUT", "indications/channel", contract, body={"useContractNum": number_}
            )
        else:
            raise GasError("unsupported_operation")
        return await self.meter(contract)

    async def submit(self, contract, expected, value, *, now):
        if now.tzinfo is None:
            raise SubmissionNotSent("invalid_submission_time")
        numeric = decimal(value)
        fresh = await self.meter(contract)
        if (
            expected.private.get("account_key") != contract.key
            or not fresh.meter
            or fresh.cycle != expected.cycle
            or fresh.previous != expected.previous
            or fresh.private["meter_changed"] != expected.private["meter_changed"]
            or not fresh.is_open(now.date())
            or fresh.private["submission_blocked"]
            or decimal(value) != int(decimal(value))
            or decimal(value) < decimal(fresh.previous)
            or decimal(value) > 99999999
            or (fresh.private["digits"] and len(str(int(numeric))) > fresh.private["digits"])
        ):
            raise SubmissionNotSent("stale_proposal")
        try:
            response = unwrap(
                await self.call(
                    "POST",
                    "relay/indications/input",
                    contract,
                    body={**self.params(contract), "thisMonthIndicatorCustomer": str(int(numeric))},
                )
            )
        except GasError:
            # The POST may have committed. The coordinator reconciles, never resends.
            raise SubmissionUncertain("submission_uncertain") from None
        if isinstance(response, dict) and response.get("inputYn") == "N":
            raise SubmissionRejected("submission_rejected")
