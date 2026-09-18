"""Observed submission wire contracts and provider acknowledgement matchers.

No payload, member name or address is persisted or included in exceptions.
Only provider-scoped business acknowledgements count as registration. Busan is
the narrow exception: its current-month receipt cannot be queried, so a valid
HTTP 200 JSON object with no non-empty outcome/error is an optimistic acknowledgement.
A later matching readback remains stronger evidence where one is available.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
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


@dataclass(frozen=True)
class SubmissionAcknowledgement:
    """A provider explicitly accepted a submission through a known matcher."""

    matcher: str


@dataclass(frozen=True)
class SubmissionAckMatcher:
    """Known response field and its provider-specific business outcomes."""

    name: str
    field: str
    accepted: frozenset[str]
    rejected: frozenset[str]


RESULT_Y_N = SubmissionAckMatcher("result_y_n", "result", frozenset({"Y"}), frozenset({"N"}))
INPUT_Y_N = SubmissionAckMatcher("input_yn", "inputYn", frozenset({"Y"}), frozenset({"N"}))
RESPONSE_CODE_OK_FAIL = SubmissionAckMatcher(
    "response_code_ok_fail", "responseCode", frozenset({"OK"}), frozenset({"FAIL"})
)
RETCD_S_E = SubmissionAckMatcher("retcd_s_e", "E_RETCD", frozenset({"S"}), frozenset({"E"}))


def match_submission_acknowledgement(
    payload: object, matchers: tuple[SubmissionAckMatcher, ...]
) -> SubmissionAcknowledgement | None:
    """Use a known matching signal; ignore absent, unknown, or conflicting signals."""
    if not isinstance(payload, dict):
        return None
    matches: list[tuple[bool, SubmissionAckMatcher]] = []
    for matcher in matchers:
        value = payload.get(matcher.field)
        if not isinstance(value, str):
            continue
        normalized = value.strip().upper()
        if normalized in matcher.accepted:
            matches.append((True, matcher))
        elif normalized in matcher.rejected:
            matches.append((False, matcher))
    if not matches or len({accepted for accepted, _ in matches}) != 1:
        return None
    accepted, matcher = matches[0]
    if not accepted:
        raise SubmissionRejected("submission_rejected")
    return SubmissionAcknowledgement(matcher.name)


def _busan_response_has_ambiguous_signal(payload: dict) -> bool:
    """Reject an unknown outcome or a populated error signal from optimistic success."""
    for field in (
        "result",
        "success",
        "ok",
        "status",
        "code",
        "inputYn",
        "responseCode",
        "E_RETCD",
    ):
        outcome = payload.get(field)
        if outcome is None:
            continue
        if isinstance(outcome, str) and not outcome.strip():
            continue
        if isinstance(outcome, (list, dict, tuple)) and not outcome:
            continue
        # Known result=Y/N was handled by match_submission_acknowledgement().
        # Any other populated outcome, including False or 0, is not evidence.
        return True
    for field in ("error", "errorCode", "errCd"):
        value = payload.get(field)
        if value is None or value is False or value == 0:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        if isinstance(value, (list, dict, tuple)) and not value:
            continue
        return True
    return False


def build_payload(
    contract,
    window: MeterWindow,
    value: int,
    html: str,
    now: datetime,
    provider=None,
    *,
    revision: bool = False,
    revision_from: str | None = None,
) -> dict:
    # Local import avoids coupling read-only parsing to the transport at startup.
    from .portal import contracts_from_html, document, opaque, portal_date

    if now.tzinfo is None:
        raise SubmissionNotSent("invalid_submission_time")
    if type(value) is not int or not 0 <= value <= 99999:
        raise SubmissionNotSent("invalid_submission_value")
    if not window.is_open(now.date()):
        raise SubmissionNotSent("window_closed")
    if revision:
        if provider is None or not provider.supports_revision_submission:
            raise SubmissionNotSent("revision_submission_unsupported")
        if revision_from is None:
            raise SubmissionNotSent("revision_submission_unavailable")
        if window.submitted is not None and decimal(window.submitted) != decimal(revision_from):
            raise SubmissionNotSent("revision_state_changed")
        if int(decimal(revision_from)) == value:
            raise SubmissionNotSent("submission_value_unchanged")
    if (window.submitted is not None and not revision) or window.private.get("submission_blocked"):
        raise SubmissionNotSent("portal_reading_present")
    if value < decimal(window.previous):
        raise SubmissionNotSent("below_official_reading")
    if not any(
        c.key == contract.key and c.bpno == contract.bpno and c.cano == contract.cano
        for c in contracts_from_html(html, provider)
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


async def send_once(
    session,
    base_url: str,
    payload: dict,
    *,
    timeout: float = 25,
    form_path: str = FORM_PATH,
    submit_path: str = SUBMIT_PATH,
    optimistic_busan: bool = False,
) -> SubmissionAcknowledgement | None:
    """One form POST, no redirects, login replay or retry of any kind."""
    try:
        async with session.post(
            base_url + submit_path,
            data=payload,
            allow_redirects=False,
            timeout=aiohttp.ClientTimeout(total=timeout),
            headers={
                "Referer": base_url + form_path,
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
            if not isinstance(result, dict):
                raise SubmissionUncertain("submission_uncertain")
            acknowledgement = match_submission_acknowledgement(result, (RESULT_Y_N,))
            if acknowledgement is not None:
                return acknowledgement
            if optimistic_busan and not _busan_response_has_ambiguous_signal(result):
                return SubmissionAcknowledgement("busan_http_200_optimistic")
            return None
    except (aiohttp.ClientError, TimeoutError):
        raise SubmissionUncertain("submission_uncertain") from None
