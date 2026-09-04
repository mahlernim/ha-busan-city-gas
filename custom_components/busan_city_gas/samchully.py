"""Experimental Samchully customer portal, including public self-meter JSON DTO.

Protocol reference: gas-self-meter-ai SamchullyClient.kt and issue #19's
index-f393362f.js analysis. Success acknowledgements require separate readback.
No image is fabricated: I_ZWSTAND_IMG is the site's manual numeric fallback.
"""

from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime

from .model import Bill, GasError, MeterWindow, decimal
from .portal import AuthenticationError, Contract, opaque
from .provider_transport import day, month, number, request, required, text, unwrap
from .submission_transport import SubmissionNotSent, SubmissionRejected, SubmissionUncertain

API_BASE = "https://ecpgw.samchully.co.kr/relay/"
READ_PATHS = frozenset(
    {
        "scl/users/me",
        "scl/services/custinfo",
        "scl/services/goji-list",
        "scl/services/meter-check",
        "scl/services/self-meter",
        "scl/services/self-meter-list",
    }
)
PUBLIC_PATHS = frozenset(
    {
        "scl/auth/login-pwd",
        "scl/auth/login",
        "scl/services/validation-tidnr",
        "scl/services/self-meter-img",
    }
)


def _object(value):
    value = unwrap(value)
    if not isinstance(value, dict):
        raise GasError("provider_schema_changed")
    return value


def _rows(value, key):
    result = _object(value).get(key)
    if not isinstance(result, list) or any(not isinstance(row, dict) for row in result):
        raise GasError("provider_schema_changed")
    return result


def _date(row, key):
    value = text(row, key)
    return day({key: re.sub(r"[./-]", "", value)}, key) if value else None


class SamchullyClient:
    """Private account identifiers stay in Contract/MeterWindow.private."""

    def __init__(self, session, data, provider):
        if provider.family != "samchully" or provider.id != "samchully":
            raise GasError("provider_mismatch")
        if any(
            not isinstance(data.get(key), str) or not data[key].strip()
            for key in ("username", "password")
        ):
            raise AuthenticationError("invalid_auth")
        self.session, self.data, self.provider = session, data, provider
        self.token = None
        self.token_expires = 0
        self.lock = asyncio.Lock()
        self.history_errors = {}

    async def _post(self, path, body, *, stage="query", authenticated=True, retry_read=True):
        if path not in (READ_PATHS if authenticated else PUBLIC_PATHS):
            raise GasError("provider_endpoint_not_allowed")
        headers = {
            "Accept": "application/json",
            "Origin": "https://cs.samchully.co.kr",
            "Referer": "https://cs.samchully.co.kr/",
        }
        if authenticated:
            await self._login()
            headers["X-User-Token"] = self.token
        try:
            return _object(
                await request(
                    self.session, "POST", API_BASE + path, headers=headers, body=body, stage=stage
                )
            )
        except AuthenticationError:
            self.token = None
            self.token_expires = 0
            # Only authenticated queries can replay once after a fresh password
            # login. Neither validation nor submission goes through this branch.
            if authenticated and retry_read:
                return await self._post(path, body, stage=stage, retry_read=False)
            raise

    async def _login(self):
        async with self.lock:
            if self.token and time.monotonic() < self.token_expires:
                return
            first = await self._post(
                "scl/auth/login-pwd",
                {
                    "userType": "PER",
                    "userId": self.data["username"],
                    "userPwd": self.data["password"],
                    "exp": 3600000,
                },
                stage="login",
                authenticated=False,
            )
            second = await self._post(
                "scl/auth/login",
                {
                    "loginToken": required(first, "loginToken"),
                    "userType": text(first, "userType") or "PER",
                    "exp": 3600000,
                },
                stage="login",
                authenticated=False,
            )
            self.token = required(second, "accessToken")
            # Request one hour and renew five minutes early; monotonic time is
            # unaffected by a system-clock correction during unattended polling.
            self.token_expires = time.monotonic() + 3300

    async def contracts(self):
        user = await self._post("scl/users/me", {}, stage="user")
        birth = re.sub(r"\D", "", required(user, "birthDate"))
        if len(birth) == 8:
            birth = birth[2:]
        if len(birth) != 6:
            raise GasError("provider_schema_changed")
        result = await self._post(
            "scl/services/custinfo",
            {
                "I_GUBUN": "1",
                "I_NAME": required(user, "userName"),
                "I_BIRTH": birth,
                "I_PHONE": re.sub(r"\D", "", required(user, "phoneNumber")),
            },
            stage="contracts",
        )
        contracts = []
        for row in _rows(result, "E_TAB"):
            cano = required(row, "VKONT")
            if not re.fullmatch(r"\d{1,40}", cano):
                raise GasError("provider_schema_changed")
            contracts.append(
                Contract(
                    opaque(f"samchully:{cano}"),
                    "",
                    cano,
                    text(row, "VKBEZ_M", "VKBEZ") or f"고객번호 {cano[-4:]}",
                    {"phone": text(row, "PHONE", "MOB_NUMBER") or ""},
                )
            )
        if not contracts or len({c.key for c in contracts}) != len(contracts):
            raise GasError("contract_selection_required")
        return contracts

    async def bills(self, contract, cached, progress=None):
        now = datetime.now()
        index = now.year * 12 + now.month - 1 - 23
        response = await self._post(
            "scl/services/goji-list",
            {
                "I_VKONT": contract.cano,
                "I_YYYYMM_FROM": f"{index // 12:04d}{index % 12 + 1:02d}",
                "I_YYYYMM_TO": now.strftime("%Y%m"),
            },
            stage="bills",
        )
        result, seen = dict(cached), set()
        self.history_errors = {}
        for row in _rows(response, "E_TAB"):
            key = month(required(row, "BILLING_PERIOD"))
            if key in seen:
                raise GasError("duplicate_bill_month")
            seen.add(key)
            start, end = _date(row, "MR_DATE_FR"), _date(row, "MR_DATE_TO")
            previous, current = number(row, "PR_ZWSTNDAB"), number(row, "ZWSTNDAB")
            if start and end and start > end:
                raise GasError("provider_date_invalid")
            if (
                previous is not None
                and current is not None
                and decimal(current) < decimal(previous)
            ):
                raise GasError("invalid_bill_segment")
            meter = text(row, "LOGIKZW", "GERNR")
            result[key] = Bill(
                key,
                number(row, "BETRW_TOT_T", "BETRW_TOT"),
                [],
                base_charge="0",
                reported_usage=number(row, "CONSUMPTION"),
                period_start=start,
                period_end=end,
                closing_reading=current,
                meter_id=opaque(f"samchully:{contract.cano}:{meter}") if meter else None,
            ).dump()
        return result

    async def meter(self, contract):
        common = {
            "I_VKONT": contract.cano,
            "I_FLAG": "1",
            "I_PHONE": re.sub(r"\D", "", contract.private.get("phone", "")),
        }
        period = _rows(await self._post("scl/services/meter-check", common), "ET_RESULT")
        target = await self._post("scl/services/self-meter", common)
        response = await self._post(
            "scl/services/self-meter-list", {"ET_VKONT": [{"VKONT": contract.cano}]}
        )
        recent_rows = response.get("E_TAB", response.get("ET_RESULT"))
        if recent_rows is None:
            recent_rows = [response]
        if len(period) != 1 or not isinstance(recent_rows, list) or len(recent_rows) != 1:
            raise GasError("meter_selection_required")
        recent = _object(recent_rows[0])
        start, end = _date(period[0], "KKO_MR_SDATE"), _date(period[0], "KKO_MR_EDATE")
        meter, previous = required(target, "E_TIDNR"), number(target, "E_PRV_M_ZWSTAND")
        state = text(recent, "E_YN")
        if not start or not end or start > end or previous is None:
            raise GasError("meter_schema_changed")
        submitted = number(recent, "E_ZWSTAND") if state in ("X", "Y") else None
        if state in ("X", "Y") and submitted is None:
            raise GasError("submission_state_unknown")
        private = {"tidnr": meter, "cano": contract.cano}
        if state not in ("X", "Y", "N"):
            private["submission_blocked"] = "submission_state_unknown"
        elif state == "N" and number(recent, "E_ZWSTAND") not in (None, "0"):
            private["submission_blocked"] = "portal_reading_present"
        return MeterWindow(
            start,
            end,
            previous,
            opaque(f"samchully:{contract.cano}:{meter}"),
            eligible=state in ("X", "Y", "N"),
            submitted=submitted,
            private=private,
        )

    async def submit(self, contract, expected, value, *, now):
        try:
            if now.tzinfo is None or type(value) is not int or not 0 <= value <= 99999:
                raise GasError("invalid_submission_value")
            matches = [
                c
                for c in await self.contracts()
                if c.key == contract.key and c.cano == contract.cano
            ]
            if len(matches) != 1:
                raise GasError("submission_contract_changed")
            fresh = await self.meter(matches[0])
            if fresh.cycle != expected.cycle or fresh.previous != expected.previous:
                raise GasError("submission_contract_changed")
            if not fresh.is_open(now.date()):
                raise GasError("window_closed")
            if fresh.submitted is not None or fresh.private.get("submission_blocked"):
                raise GasError("portal_reading_present")
            if value < decimal(fresh.previous):
                raise GasError("below_official_reading")
            common = {"I_VKONT": contract.cano, "I_TIDNR": fresh.private["tidnr"], "I_ROLGB": "MI"}
            for step in ("1", "2"):
                check = await self._post(
                    "scl/services/validation-tidnr",
                    {
                        **common,
                        "I_GUBUN": step,
                        **({"I_ZWSTAND": str(value)} if step == "2" else {}),
                    },
                    stage="preflight",
                    authenticated=False,
                )
                if text(check, "E_RETCD") != "S":
                    raise GasError("submission_preflight_rejected")
                if step == "1" and (
                    number(check, "E_ZWSTAND") is None
                    or decimal(number(check, "E_ZWSTAND")) != decimal(fresh.previous)
                ):
                    raise GasError("submission_contract_changed")
        except GasError as error:
            raise SubmissionNotSent(str(error)) from None
        try:
            response = await self._post(
                "scl/services/self-meter-img",
                {
                    "I_VKONT": contract.cano,
                    "I_TIDNR": fresh.private["tidnr"],
                    "I_ZWSTAND": str(value),
                    "I_ZWSTAND_IMG": str(value),
                    "I_TMP_METER_YN": "N",
                },
                stage="submission",
                authenticated=False,
            )
            status = text(response, "E_RETCD")
        except GasError:
            raise SubmissionUncertain("submission_uncertain") from None
        if status == "E":
            raise SubmissionRejected("submission_rejected")
        if status != "S":
            raise SubmissionUncertain("submission_uncertain")
