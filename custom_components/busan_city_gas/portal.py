"""Unsupported portal adapter. Fail closed on changed markup; never log responses."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime

import aiohttp
from bs4 import BeautifulSoup

from .const import BASE_URL, SUBMISSION_ENABLED
from .model import Bill, GasError, MeterWindow, Segment, Tariff, decimal
from .provider import Provider, default_profile, get_provider, profile_match


class AuthenticationError(GasError):
    pass


class ConnectionError(GasError):
    pass


def opaque(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:24]


@dataclass
class Contract:
    key: str
    bpno: str
    cano: str
    label: str


def document(html: str) -> BeautifulSoup:
    soup = BeautifulSoup(html, "html.parser")
    if soup.select_one('input[type="password"]'):
        raise AuthenticationError("reauth_required")
    if soup.title and "error" in soup.title.get_text().lower():
        raise GasError("portal_error")
    return soup


def contracts_from_html(html: str, provider: Provider | None = None) -> list[Contract]:
    provider = provider or get_provider("busan")
    soup = document(html)
    bp = re.search(r'BPNO\s*:\s*["\'](\d+)["\']', html)
    if not bp:
        raise GasError("contract_schema_changed")
    result = []
    for node in soup.select('input[id^="list_cano_"]'):
        cano = node.get("value", "")
        if not cano.isdigit():
            raise GasError("contract_schema_changed")
        key = opaque(f"{provider.code}:{bp[1]}:{cano}")
        if not any(c.key == key for c in result):
            result.append(Contract(key, bp[1], cano, f"계약 {cano}"))
    return result


def portal_date(value: str) -> str:
    try:
        return date.fromisoformat(str(value).replace(".", "-").replace("/", "-")).isoformat()
    except ValueError:
        raise GasError("date_schema_changed") from None


def meter_from_json(payload: dict) -> MeterWindow:
    rows = payload.get("list")
    if not isinstance(rows, list) or len(rows) != 1:
        raise GasError("meter_selection_required")
    row = rows[0]
    required = [
        "START_DATE",
        "END_DATE",
        "LAST_READINGRESULT",
        "SELF_READ_YN",
        "selfReadYn",
    ]
    if not all(key in row for key in required):
        raise GasError("meter_schema_changed")
    # The live self-reading endpoint spells this CERAET; some fixtures/site
    # variants use GERAET. Never silently choose between conflicting IDs.
    meter = row.get("CERAET") or row.get("GERAET")
    if not meter or (row.get("CERAET") and row.get("GERAET") and row["CERAET"] != row["GERAET"]):
        raise GasError("meter_schema_changed")
    start, end = portal_date(row["START_DATE"]), portal_date(row["END_DATE"])
    if start > end:
        raise GasError("invalid_window")
    submitted = None
    blocked = {}
    if row["SELF_READ_YN"] == "Y":
        # Do not treat default 0 as submitted when SELF_READ_YN is N.
        submitted = str(decimal(row.get("CUST_READING_RESULT")))
    elif row["SELF_READ_YN"] != "N":
        raise GasError("submission_state_unknown")
    elif decimal(row.get("CUST_READING_RESULT") or "0") > 0:
        # The actual page disables the entry control in this state, even though
        # SELF_READ_YN is N. Do not overwrite it or call it a confirmed acceptance.
        blocked = {
            "submission_blocked": "portal_reading_present",
            "reported_reading": str(decimal(row["CUST_READING_RESULT"])),
        }
    return MeterWindow(
        start,
        end,
        str(decimal(row["LAST_READINGRESULT"])),
        opaque(str(meter)),
        portal_date(row["ADATSOLL1"]) if row.get("ADATSOLL1") else None,
        row["selfReadYn"] == "Y",
        submitted,
        {
            key: row[key]
            for key in ("CERAET", "GERAET", "ANLAGE", "ADATSOLL1", "V_LDO", "CANO", "BPNO")
            if key in row
        }
        | blocked,
    )


def available_months(html: str) -> list[str]:
    document(html)
    return sorted(set(re.findall(r'fnGetAskDetail\([^)]*["\'](20\d{4})["\']', html)))


def _period(month: str, value: str) -> tuple[str, str]:
    match = re.fullmatch(r"(\d{2})\.(\d{2})~(\d{2})\.(\d{2})", value)
    if not match:
        raise GasError("period_schema_changed")
    sm, sd, em, ed = map(int, match.groups())
    year, billed_month = int(month[:4]), int(month[4:])
    end_year = year - (em > billed_month)
    end = date(end_year, em, ed)
    start = date(end_year - (sm > em), sm, sd)
    return start.isoformat(), end.isoformat()


def bill_from_html(html: str, month: str | None = None) -> Bill:
    soup = document(html)
    selection = soup.select_one("#budat")
    selected = selection.get("value", "") if selection else ""
    if not re.fullmatch(r"20\d{4}", selected) or (month and selected != month):
        raise GasError("bill_month_mismatch")
    tables = soup.select("table")
    if len(tables) < 5:
        raise GasError("bill_schema_changed")

    def cells(tr):
        return [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"], recursive=False)]

    summary = dict(row for tr in tables[0].select("tr") if len(row := cells(tr)) == 2)
    if "합계" not in summary:
        raise GasError("bill_schema_changed")
    segments, price_lines = [], []
    base = decimal(0)
    meter = ""
    unsupported = False
    for tr in tables[4].select("tr"):
        row = cells(tr)
        if len(row) != 11:
            continue
        if row[1]:
            meter = opaque(row[1])
        if (
            re.fullmatch(r"\d{2}\.\d{2}~\d{2}\.\d{2}", row[2])
            and re.fullmatch(r"[\d,.]+", row[3])
            and re.fullmatch(r"[\d,.]+", row[4])
        ):
            start, end = _period(selected, row[2])
            segment = Segment(
                start,
                end,
                str(decimal(row[3])),
                str(decimal(row[4])),
                str(decimal(row[5])),
                str(decimal(row[7])),
                meter,
            )
            if segment.usage * decimal(segment.coefficient) != decimal(
                row[6]
            ) or segment.heat != decimal(row[8]):
                raise GasError("bill_arithmetic_changed")
            segments.append(segment)
        if "기본료" in row[0] or "기본요금" in row[0]:
            base += decimal(row[10])
        if any(label in row[0] for label in ("할인", "경감", "정산", "교체비")):
            unsupported = True
        if re.fullmatch(r"[\d,.]+", row[8]) and re.fullmatch(r"[\d,.]+", row[9]):
            price_lines.append((str(decimal(row[8])), str(decimal(row[9]))))
    if not segments or not price_lines:
        raise GasError("bill_schema_changed")
    due = None
    for tr in soup.select("tr"):
        row = cells(tr)
        if len(row) == 2 and row[0] in ("납기일", "납부기한"):
            match = re.search(r"20\d{2}[./-]\d{2}[./-]\d{2}", row[1])
            if match:
                due = portal_date(match[0])
    bill = Bill(
        selected, str(decimal(summary["합계"])), segments, str(base), price_lines, due, unsupported
    )
    if bill.reconstructed() != decimal(bill.amount):
        bill.unsupported_adjustments = True
    return bill


def tariff_from_html(
    html: str, provider: Provider | None = None, tariff_profile: str | None = None
) -> Tariff:
    provider = provider or get_provider("busan")
    tariff_profile = tariff_profile or default_profile(provider)
    match = profile_match(provider, tariff_profile)
    soup = document(html)
    text = soup.get_text(" ", strip=True)
    effective = re.search(r"20\d{2}-\d{2}-\d{2}", text)
    row_nodes = soup.select("tr")
    rows = [" ".join(node.stripped_strings) for node in row_nodes]

    def numbers(value: str) -> list[str]:
        return re.findall(r"(?<!\d)(?:\d{1,3}(?:,\d{3})+|\d+\.\d+)(?!\d)", value)

    matched = [row for row in rows if match in row]
    if not effective or (not provider.threshold_mj and len(matched) != 1):
        raise GasError("tariff_schema_changed")
    base = "0"
    for node in row_nodes:
        node_text = node.get_text(" ", strip=True)
        if match not in node_text and not (provider.threshold_mj and "주택" in node_text):
            continue
        numeric_cells = [
            cell.get_text(strip=True).replace(",", "")
            for cell in node.find_all(["th", "td"], recursive=False)
            if re.fullmatch(r"[\d,.]+", cell.get_text(strip=True))
        ]
        if len(numeric_cells) >= 2:
            base = numeric_cells[-2]
            break
    if provider.threshold_mj:
        threshold = provider.threshold_mj
        lower = next(
            (row for row in rows if threshold in row and ("까지" in row or "이하" in row)), None
        )
        upper = next((row for row in rows if threshold in row and "초과" in row), None)
        if lower and upper and numbers(lower) and numbers(upper):
            bands = [
                {"up_to_mj": threshold, "rate": numbers(lower)[-1].replace(",", "")},
                {"up_to_mj": None, "rate": numbers(upper)[-1].replace(",", "")},
            ]
        else:
            raise GasError("tariff_schema_changed")
    else:
        rates = [numbers(row)[-1].replace(",", "") for row in matched if numbers(row)]
        if len(rates) != 1:
            raise GasError("tariff_schema_changed")
        bands = [{"up_to_mj": None, "rate": rates[0]}]
    return Tariff(portal_date(effective[0]), bands, base, tariff_profile)


def caloric_from_json(
    payload: dict, start: str, end: str, provider: Provider | None = None
) -> dict:
    provider = provider or get_provider("busan")
    rows = payload.get("list")
    if not isinstance(rows, list) or len(rows) != 1:
        raise GasError("heat_schema_changed")
    row = rows[0]
    try:
        first, last = portal_date(row["O_FDATE"]), portal_date(row["O_TDATE"])
        factor = decimal(row["E_CALOR"])
        if not start <= first <= last <= end or factor == 0 or row.get("I_CALOR") != provider.code:
            raise GasError("heat_coverage_invalid")
        return {
            "start": first,
            "end": last,
            "factor": str(factor),
            "partial": first != start or last != end,
        }
    except (KeyError, TypeError):
        raise GasError("heat_schema_changed") from None


class PortalClient:
    """One isolated cookie jar per account, bounded reads, no browser dependency."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        username: str,
        password: str,
        provider: Provider | None = None,
        tariff_region: str = "default",
        tariff_profile: str | None = None,
    ):
        self.session = session
        self.username, self.password = username, password
        self.provider = provider or get_provider("busan")
        self.tariff_region = tariff_region
        self.tariff_profile = tariff_profile or default_profile(self.provider)
        self.authenticated = False
        self.lock = asyncio.Lock()
        self.history_errors: dict[str, list[str]] = {}

    async def _request(self, path: str, data: dict | None = None) -> str:
        try:
            async with self.session.request(
                "POST" if data is not None else "GET",
                BASE_URL + path,
                data=data,
                allow_redirects=False,
                timeout=aiohttp.ClientTimeout(total=25),
                headers={"Referer": BASE_URL + f"/{self.provider.path}/main/index.do"},
            ) as response:
                if response.status in (301, 302, 303, 307, 308):
                    raise AuthenticationError("reauth_required")
                response.raise_for_status()
                body = await response.read()
                if len(body) > 4_000_000:
                    raise GasError("response_too_large")
                return body.decode("utf-8", errors="replace")
        except (aiohttp.ClientError, TimeoutError):
            raise ConnectionError("cannot_connect") from None

    async def login(self) -> None:
        self.authenticated = False
        await self._request(f"/{self.provider.path}/login/login.do")
        text = await self._request(
            f"/{self.provider.path}/login/loginProcess.do",
            {
                "id": self.username,
                "pw": self.password,
                "returnURL": f"/{self.provider.path}/read/selfRead.do",
            },
        )
        try:
            result = json.loads(text)
        except ValueError:
            raise GasError("login_schema_changed") from None
        if result.get("errCd") != "S":
            raise AuthenticationError("invalid_auth")
        self.authenticated = True

    async def read(self, path: str, data: dict | None = None) -> str:
        async with self.lock:
            if not self.authenticated:
                await self.login()
            try:
                text = await self._request(path, data)
                if not text.lstrip().startswith(("{", "[")):
                    document(text)
                return text
            except AuthenticationError:
                await self.login()  # Read-only replay, once; never used for writes.
                text = await self._request(path, data)
                if not text.lstrip().startswith(("{", "[")):
                    document(text)
                return text

    async def contracts(self) -> list[Contract]:
        return contracts_from_html(
            await self.read(f"/{self.provider.path}/read/selfRead.do"), self.provider
        )

    async def meter(self, contract: Contract) -> MeterWindow:
        text = await self.read(
            f"/{self.provider.path}/read/call_EBPP_018.do",
            {"CANO": contract.cano, "BPNO": contract.bpno},
        )
        try:
            return meter_from_json(json.loads(text))
        except (ValueError, TypeError, KeyError):
            raise GasError("meter_schema_changed") from None

    async def bill_page(self, contract: Contract, month: str = "") -> str:
        return await self.read(
            f"/{self.provider.path}/charge/askDetail.do",
            {
                "bpno": contract.bpno,
                "cano": contract.cano,
                "compcd": self.provider.code,
                "GUBUN": "02",
                "date": month,
            },
        )

    async def bills(
        self, contract: Contract, cached: dict[str, dict], *, progress=None
    ) -> dict[str, dict]:
        text = await self.bill_page(contract)
        latest = bill_from_html(text)
        result = {**cached, latest.month: latest.dump()}
        months = available_months(text)
        total = len(set(months) | {latest.month})
        if progress:
            progress(result, sum(m in result for m in set(months) | {latest.month}), total)
        self.history_errors[contract.key] = []
        for month in months:
            if month not in result:
                try:
                    result[month] = bill_from_html(
                        await self.bill_page(contract, month), month
                    ).dump()
                    if progress:
                        progress(
                            result, sum(m in result for m in set(months) | {latest.month}), total
                        )
                except AuthenticationError:
                    raise
                except GasError:
                    self.history_errors[contract.key].append(month)
                    break  # Keep current bill; retry history on the next daily read.
                await asyncio.sleep(0.25)
        return result

    async def tariff(self) -> Tariff:
        data = None
        if self.provider.id == "koone":
            data = {"regionSeq": "275" if self.tariff_region == "gyeonggi" else "274", "seq": "0"}
        return tariff_from_html(
            await self._request(f"/{self.provider.path}/rate/guide.do", data),
            self.provider,
            self.tariff_profile,
        )

    async def caloric(self, start: str, end: str) -> dict:
        text = await self._request(
            f"/{self.provider.path}/caloric/call_EBPP_044.do",
            {
                "I_FDATE": start.replace("-", ""),
                "I_TDATE": end.replace("-", ""),
                "I_CALOR": self.provider.code,
            },
        )
        try:
            return caloric_from_json(json.loads(text), start, end, self.provider)
        except ValueError:
            raise GasError("heat_schema_changed") from None

    async def submit(
        self, contract: Contract, window: MeterWindow, value: int, *, now: datetime
    ) -> None:
        # Emergency switch, independent of the per-request validation below.
        if not SUBMISSION_ENABLED:
            raise GasError("submission_disabled")
        from .submission_transport import SubmissionNotSent, build_payload, send_once

        try:
            form_path = f"/{self.provider.path}/read/selfRead.do"
            submit_path = f"/{self.provider.path}/read/insertSelfRead.do"
            html = await self.read(form_path)
            payload = build_payload(contract, window, value, html, now, self.provider)
        except GasError as error:
            raise SubmissionNotSent(str(error)) from None
        async with self.lock:
            if not self.authenticated:
                raise SubmissionNotSent("reauth_required")
            # Never use read(): it retries authenticated reads after login.
            if self.provider.id == "busan":
                await send_once(self.session, BASE_URL, payload)
            else:
                await send_once(
                    self.session, BASE_URL, payload, form_path=form_path, submit_path=submit_path
                )
