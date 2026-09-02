"""Observed portal wire contract. Production remains gated in PortalClient.

No payload, member name or address is persisted or included in exceptions.
The browser's Y acknowledgement is NOT proof of a matching registered reading.
"""

from __future__ import annotations

import json
from datetime import datetime

import aiohttp

from .model import GasError, MeterWindow, decimal

SUBMIT_PATH = "/busan/read/insertSelfRead.do"
FORM_PATH = "/busan/read/selfRead.do"
MAX_RESPONSE_BYTES = 64 * 1024


class SubmissionNotSent(GasError):
    """Local/preflight failure: the write endpoint was not called."""


class SubmissionRejected(GasError):
    """The server explicitly returned N; still reconcile the readback."""


class SubmissionUncertain(GasError):
    """A write may have happened. Never retry the POST automatically."""


def build_payload(contract, window: MeterWindow, value: int, html: str, now: datetime) -> dict:
    # Local import avoids coupling read-only parsing to the transport at startup.
    from .portal import contracts_from_html, document, opaque, portal_date

    if now.tzinfo is None:
        raise SubmissionNotSent("invalid_submission_time")
    if type(value) is not int or not 0 <= value <= 99999:
        raise SubmissionNotSent("invalid_submission_value")
    if not window.is_open(now.date()):
        raise SubmissionNotSent("window_closed")
    if window.submitted is not None or window.private.get("submission_blocked"):
        raise SubmissionNotSent("portal_reading_present")
    if value < decimal(window.previous):
        raise SubmissionNotSent("below_official_reading")
    if not any(
        c.key == contract.key and c.bpno == contract.bpno and c.cano == contract.cano
        for c in contracts_from_html(html)
    ):
        raise SubmissionNotSent("submission_contract_changed")
    soup = document(html)
    matches = [n for n in soup.select('input[id^="list_cano_"]') if n.get("value") == contract.cano]
    if len(matches) != 1:
        raise SubmissionNotSent("submission_contract_changed")
    index = matches[0]["id"].removeprefix("list_cano_")
    owner = soup.find("input", id=f"list_bpname_{index}")
    if owner is None or not owner.get("value", "").strip():
        raise SubmissionNotSent("submission_metadata_missing")
    private = window.private
    meter = str(private.get("CERAET") or private.get("GERAET") or "")
    if not meter or ";" in meter or opaque(meter) != window.meter:
        raise SubmissionNotSent("submission_contract_changed")
    if (
        private.get("CANO", contract.cano) != contract.cano
        or private.get("BPNO", contract.bpno) != contract.bpno
    ):
        raise SubmissionNotSent("submission_contract_changed")
    if any(private.get(k) in (None, "") for k in ("ANLAGE", "V_LDO", "ADATSOLL1")):
        raise SubmissionNotSent("submission_metadata_missing")
    if window.planned != portal_date(private["ADATSOLL1"]):
        raise SubmissionNotSent("submission_contract_changed")
    # The inspected page references geraet_addr_0 but does not render it. jQuery
    # encodes its undefined value as an empty form field. Do not substitute a
    # different private address field without open-window validation.
    address = soup.find("input", id="geraet_addr_0")
    return {
        "bpno": contract.bpno,
        "name": owner["value"],
        "cano": contract.cano,
        "sernr": meter,
        "addr": address.get("value", "") if address else "",
        "cust_readingresult": str(value),
        "adatsoll1": str(private["ADATSOLL1"]),
        "v_ldo": str(private["V_LDO"]),
        "anlage": str(private["ANLAGE"]),
    }


async def send_once(session, base_url: str, payload: dict, *, timeout: float = 25) -> None:
    """One form POST, no redirects, login replay or retry of any kind."""
    try:
        async with session.post(
            base_url + SUBMIT_PATH,
            data=payload,
            allow_redirects=False,
            timeout=aiohttp.ClientTimeout(total=timeout),
            headers={
                "Referer": base_url + FORM_PATH,
                "Origin": base_url,
                "X-Requested-With": "XMLHttpRequest",
            },
        ) as response:
            if response.status != 200:
                raise SubmissionUncertain("submission_uncertain")
            raw = bytearray()
            async for chunk in response.content.iter_chunked(4096):
                raw.extend(chunk)
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise SubmissionUncertain("submission_uncertain")
            try:
                result = json.loads(raw)
            except (ValueError, UnicodeError):
                raise SubmissionUncertain("submission_uncertain") from None
            if not isinstance(result, dict) or not isinstance(result.get("result"), str):
                raise SubmissionUncertain("submission_uncertain")
            status = result["result"].strip()
            if status == "N":
                raise SubmissionRejected("submission_rejected")
            if status != "Y":
                raise SubmissionUncertain("submission_uncertain")
    except (aiohttp.ClientError, TimeoutError):
        raise SubmissionUncertain("submission_uncertain") from None
