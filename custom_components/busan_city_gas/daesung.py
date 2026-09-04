"""Experimental Daesung HTML adapters with runtime labelled-form discovery.

Known public routes are company-specific. Customer HTML is discovered after the
user authenticates; ambiguous forms fail without sending a meter submission.
"""

from __future__ import annotations

import asyncio
import re
from datetime import date
from urllib.parse import urlencode, urljoin, urlsplit

import aiohttp
from bs4 import BeautifulSoup

from .model import Bill, GasError, MeterWindow, decimal
from .portal import AuthenticationError, ConnectionError, Contract, opaque
from .submission_transport import SubmissionNotSent, SubmissionUncertain

ENDPOINTS = {
    "daesung": ("https://cyber.daesungenergy.com", "/consult/self"),
    "daesungclean": ("https://www.daesungcleanenergy.co.kr", "/service/self_input"),
}
CONTRACT = re.compile(r"고객번호|사용계약번호|납부자번호|계약번호|수용가번호")
READING = re.compile(r"당월지침|현재지침|금월지침|검침지침|자가검침값|검침값")
FORBIDDEN = re.compile(r"해지|취소|결제|납부|가입|신청|cancel|request|payment|enroll", re.I)
DATE = r"(20\d{2})[.년/-]\s*(\d{1,2})[.월/-]\s*(\d{1,2})"


def _normal(value):
    return re.sub(r"\s+", "", value)


def _label(soup, control):
    parts = [
        control.get("title", ""),
        control.get("aria-label", ""),
        control.get("placeholder", ""),
    ]
    if control.get("id"):
        parts.extend(
            n.get_text(" ", strip=True)
            for n in soup.find_all("label", attrs={"for": control["id"]})
        )
    parent_label = control.find_parent("label")
    if parent_label:
        parts.append(parent_label.get_text(" ", strip=True))
    cell = control.find_parent("td")
    if cell:
        heading = cell.find_previous_sibling("th")
        if heading:
            parts.append(heading.get_text(" ", strip=True))
    return _normal(" ".join(parts))


def _doc(html):
    soup = BeautifulSoup(html, "html.parser")
    if soup.select_one('input[type="password"]') or re.search(
        r"로그인\s*(?:되어|후|이 필요)|/users/login",
        " ".join(s.get_text() for s in soup.select("script")),
    ):
        raise AuthenticationError("reauth_required")
    return soup


def _pairs(soup):
    result = {}
    for row in soup.select("tr"):
        cells = row.find_all(["th", "td"], recursive=False)
        for index in range(0, len(cells) - 1, 2):
            if cells[index].name == "th":
                value_node = cells[index + 1].find("input")
                result[_normal(cells[index].get_text())] = (
                    value_node.get("value", "")
                    if value_node
                    else cells[index + 1].get_text(" ", strip=True)
                )
    for node in soup.select("input[name]"):
        label = _label(soup, node)
        if label:
            result[label] = node.get("value", "")
    return result


def _field(values, pattern, required=True):
    found = {v for k, v in values.items() if re.search(pattern, k) and v.strip()}
    if len(found) != 1:
        if not found and not required:
            return None
        raise GasError("provider_schema_changed")
    return found.pop()


def _number(value):
    match = re.fullmatch(r"\s*([\d,]+(?:\.\d+)?)\s*(?:원|㎥|m³|m3)?\s*", value)
    if not match:
        raise GasError("provider_number_invalid")
    number = decimal(match[1])
    if number > 99999999:
        raise GasError("provider_number_invalid")
    return str(number)


def _dates(value):
    try:
        return [date(*map(int, item)).isoformat() for item in re.findall(DATE, value)]
    except ValueError:
        raise GasError("provider_date_invalid") from None


def contracts_from_html(html, provider, username):
    soup = _doc(html)
    candidates = [
        n for n in soup.select("select[name],input[name]") if CONTRACT.search(_label(soup, n))
    ]
    if len(candidates) != 1:
        raise GasError("contract_selection_required")
    node = candidates[0]
    choices = node.find_all("option") if node.name == "select" else [node]
    result = []
    for choice in choices:
        value = choice.get("value", "").strip()
        if not value:
            continue
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", value):
            raise GasError("contract_schema_changed")
        result.append(
            Contract(
                opaque(f"{provider.id}:{username}:{value}"),
                opaque(f"{provider.id}:{username}"),
                value,
                choice.get_text(" ", strip=True) or f"고객번호 {value[-4:]}",
                {"selector": node["name"]},
            )
        )
    if not result or len({c.key for c in result}) != len(result):
        raise GasError("contract_selection_required")
    return result


def bills_from_html(html):
    soup, bills = _doc(html), {}
    for table in soup.select("table"):
        headers = [_normal(n.get_text()) for n in table.select("tr th")]
        if not any(re.search(r"청구월|사용월|년월|고지월", h) for h in headers):
            continue
        # Header rows only; key-value detail tables are not monthly ledgers.
        first = table.find("tr")
        headers = [_normal(n.get_text()) for n in first.find_all(["th", "td"], recursive=False)]
        for row in table.find_all("tr")[1:]:
            cells = row.find_all("td", recursive=False)
            if len(cells) != len(headers):
                continue
            values = dict(zip(headers, (c.get_text(" ", strip=True) for c in cells), strict=True))
            raw_month = _field(values, r"청구월|사용월|년월|고지월", False)
            if not raw_month:
                continue
            digits = re.sub(r"\D", "", raw_month)
            if not re.fullmatch(r"20\d{4}", digits):
                raise GasError("provider_date_invalid")
            try:
                date(int(digits[:4]), int(digits[4:]), 1)
            except ValueError:
                raise GasError("provider_date_invalid") from None
            if digits in bills:
                raise GasError("duplicate_bill_month")
            amount = _field(values, r"청구금액|고지금액|합계금액|당월요금", False)
            usage = _field(values, r"사용량", False)
            current = _field(values, r"당월지침|현재지침|금월지침", False)
            bills[digits] = Bill(
                digits,
                _number(amount) if amount else None,
                [],
                base_charge="0",
                reported_usage=_number(usage) if usage else None,
                closing_reading=_number(current) if current else None,
            ).dump()
    if not bills:
        raise GasError("bill_schema_changed")
    return bills


class DaesungClient:
    def __init__(self, session, data, provider):
        if provider.id not in ENDPOINTS or provider.family != "daesung":
            raise GasError("provider_mismatch")
        if any(
            not isinstance(data.get(k), str) or not data[k].strip()
            for k in ("username", "password")
        ):
            raise AuthenticationError("invalid_auth")
        self.session, self.data, self.provider = session, data, provider
        self.base, self.meter_path = ENDPOINTS[provider.id]
        self.authenticated = False
        self.lock = asyncio.Lock()
        self.history_errors = {}

    def _path(self, value, source="/"):
        parsed = urlsplit(urljoin(self.base + source, value))
        if (
            parsed.scheme != "https"
            or parsed.netloc != urlsplit(self.base).netloc
            or parsed.fragment
        ):
            raise GasError("provider_endpoint_not_allowed")
        return parsed.path + ("?" + parsed.query if parsed.query else "")

    async def _request(self, path, data=None, *, write=False):
        path = self._path(path)
        try:
            async with self.session.request(
                "POST" if data is not None else "GET",
                self.base + path,
                data=data,
                allow_redirects=False,
                timeout=aiohttp.ClientTimeout(total=30),
                headers={"Origin": self.base, "Referer": self.base + path},
            ) as response:
                if 300 <= response.status < 400:
                    if write:
                        raise SubmissionUncertain("submission_uncertain")
                    location = self._path(response.headers.get("Location", ""), path)
                    if location.startswith("/users/login"):
                        raise AuthenticationError("reauth_required")
                    # Redirects are not automatically replayed. Login success is
                    # checked by the following explicit monthly-page GET.
                    if path == "/users/login" and data is not None:
                        return ""
                    raise GasError("provider_redirect_changed")
                if response.status in (401, 403):
                    raise AuthenticationError("reauth_required")
                if response.status != 200:
                    raise GasError("provider_http_error")
                raw = bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    raw.extend(chunk)
                    if len(raw) > 4000000:
                        raise GasError("provider_response_too_large")
                return bytes(raw).decode("utf-8", errors="replace")
        except (aiohttp.ClientError, TimeoutError):
            raise ConnectionError("cannot_connect") from None

    async def _login(self):
        self.authenticated = False
        soup = BeautifulSoup(await self._request("/users/login"), "html.parser")
        form = soup.find("form", id="loginForm")
        if not form or self._path(form.get("action", ""), "/users/login") != "/users/login":
            raise GasError("login_schema_changed")
        if not all(form.find("input", attrs={"name": name}) for name in ("id", "password")):
            raise GasError("login_schema_changed")
        body = {n["name"]: n.get("value", "") for n in form.select('input[type="hidden"][name]')}
        body.update(
            id=self.data["username"], password=self.data["password"], returl="/charge/month"
        )
        reply = await self._request("/users/login", body)
        if "일치하지 않습니다" in reply or "로그인 실패" in reply:
            raise AuthenticationError("invalid_auth")
        _doc(await self._request("/charge/month"))
        self.authenticated = True

    async def _read(self, path):
        async with self.lock:
            if not self.authenticated:
                await self._login()
            try:
                html = await self._request(path)
                _doc(html)
                return html
            except AuthenticationError:
                await self._login()
                html = await self._request(path)
                _doc(html)
                return html

    async def contracts(self):
        return contracts_from_html(
            await self._read("/charge/month"), self.provider, self.data["username"]
        )

    async def _page(self, contract, path):
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,79}", contract.private.get("selector", "")):
            raise GasError("contract_schema_changed")
        html = await self._read(
            path + "?" + urlencode({contract.private["selector"]: contract.cano})
        )
        nodes = _doc(html).find_all(attrs={"name": contract.private["selector"]})
        found = set()
        for node in nodes:
            if node.name == "select":
                selected = node.select("option[selected]")
                if len(selected) == 1:
                    found.add(selected[0].get("value"))
            else:
                found.add(node.get("value"))
        if found != {contract.cano}:
            raise GasError("submission_contract_changed")
        return html

    async def bills(self, contract, cached, progress=None):
        return {**cached, **bills_from_html(await self._page(contract, "/charge/month"))}

    def _meter_form(self, html, contract):
        soup = _doc(html)
        forms = []
        for form in soup.select("form"):
            inputs = [
                n
                for n in form.select("input[name]")
                if n.get("type", "text") not in ("hidden", "submit", "button")
                and READING.search(_label(soup, n))
            ]
            if len(inputs) != 1 or inputs[0].has_attr("disabled") or inputs[0].has_attr("readonly"):
                continue
            if inputs[0].get("value", "").strip():
                # Existing values may be an accepted or pending submission;
                # never silently overwrite them based on an editable control.
                continue
            if str(form.get("method", "get")).lower() != "post":
                continue
            action = self._path(form.get("action", ""), self.meter_path)
            if not action.split("?", 1)[0].startswith(self.meter_path) or FORBIDDEN.search(action):
                continue
            # Only explicit submission controls; never click or infer enrollment.
            buttons = [
                b
                for b in form.select('button,input[type="submit"]')
                if not b.has_attr("disabled")
                and str(b.get("type", "submit")).lower() == "submit"
                and re.search(r"검침|입력|등록|저장", b.get("value", "") + b.get_text())
                and not FORBIDDEN.search(b.get("value", "") + b.get_text())
            ]
            if len(buttons) != 1:
                continue
            fields = {
                n["name"]: n.get("value", "") for n in form.select('input[type="hidden"][name]')
            }
            if buttons[0].get("name"):
                fields[buttons[0]["name"]] = buttons[0].get("value", "")
            selected = form.find(attrs={"name": contract.private["selector"]})
            if not selected:
                continue
            val = selected.get("value")
            if selected.name == "select":
                options = selected.select("option[selected]")
                val = options[0].get("value") if len(options) == 1 else None
            if val != contract.cano:
                continue
            fields[contract.private["selector"]] = contract.cano
            forms.append((action, fields, inputs[0]["name"]))
        if len(forms) != 1:
            raise GasError("meter_form_schema_changed")
        return forms[0]

    def _window(self, html, contract):
        soup = _doc(html)
        values = _pairs(soup)
        period = _field(values, r"검침기간|입력기간")
        dates = _dates(period)
        if len(dates) != 2 or dates[0] > dates[1]:
            raise GasError("meter_schema_changed")
        previous = _number(_field(values, r"전월지침|이전지침|전회지침"))
        identity = _field(values, r"계량기번호|기물번호")
        submitted_raw = _field(values, r"등록지침|제출지침|입력완료지침", False)
        submitted = _number(submitted_raw) if submitted_raw else None
        # Discoverability is eligibility; closed/read-only forms cannot submit.
        private = {}
        try:
            action, fields, reading = self._meter_form(html, contract)
            private = {"action": action, "fields": fields, "reading": reading}
        except GasError:
            private["submission_blocked"] = "meter_form_schema_changed"
        return MeterWindow(
            dates[0],
            dates[1],
            previous,
            opaque(f"{self.provider.id}:{contract.cano}:{identity}"),
            eligible="action" in private,
            submitted=submitted,
            private=private,
        )

    async def meter(self, contract):
        return self._window(await self._page(contract, self.meter_path), contract)

    async def submit(self, contract, expected, value, *, now):
        try:
            if now.tzinfo is None or type(value) is not int or not 0 <= value <= 99999:
                raise GasError("invalid_submission_value")
            if not any(
                c.key == contract.key and c.cano == contract.cano for c in await self.contracts()
            ):
                raise GasError("submission_contract_changed")
            fresh = await self.meter(contract)
            if fresh.cycle != expected.cycle or fresh.previous != expected.previous:
                raise GasError("submission_contract_changed")
            if not fresh.is_open(now.date()) or fresh.private.get("submission_blocked"):
                raise GasError("window_closed")
            if fresh.submitted is not None:
                raise GasError("portal_reading_present")
            if value < decimal(fresh.previous):
                raise GasError("below_official_reading")
            body = {**fresh.private["fields"], fresh.private["reading"]: str(value)}
        except (GasError, ValueError):
            raise SubmissionNotSent("submission_preflight_failed") from None
        try:
            await self._request(fresh.private["action"], body, write=True)
        except GasError:
            raise SubmissionUncertain("submission_uncertain") from None
        # Unknown acknowledgements are never invented as success. Coordinator
        # reconciles the official meter page after this one write attempt.
        raise SubmissionUncertain("submission_uncertain")
