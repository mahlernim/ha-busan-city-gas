"""Synthetic Haeyang mobile-web protocol tests; no live customer requests."""

from copy import deepcopy
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.busan_city_gas import haeyang
from custom_components.busan_city_gas.model import GasError
from custom_components.busan_city_gas.portal import AuthenticationError
from custom_components.busan_city_gas.submission import SubmissionManager
from custom_components.busan_city_gas.submission_transport import (
    SubmissionNotSent,
    SubmissionUncertain,
)


@pytest.fixture
def wire(monkeypatch):
    calls = []
    responses = {
        "LOGIN": {
            "legacy_message": haeyang.envelope(
                "LOGIN01",
                {"payerList": [{"PAYERNO": "123", "PAYERNM": "Synthetic", "INSTALLNO": "456"}]},
            )
        },
        "MYPAGE3": {"payerList": [{"PAYERNO": "123", "RETCODE": "00", "ADDRESS": "Synthetic"}]},
        "BILL001": {
            "IT_TAB": [
                {
                    "YEARMONTH": "202608",
                    "NOTICE_AMT": "123.45",
                    "CONSUME_QTY": "9.2",
                    "NOTICENO": "bill",
                }
            ]
        },
        "BILL002": {
            "IT_TAB": [
                {
                    "USE_PERIOD_FROM": "20260710",
                    "USE_PERIOD_TO": "20260809",
                    "INSTALLNO": "456",
                    "CURR_INDCT": "100",
                    "CONSUME_QTY": "9.2",
                }
            ]
        },
        "SELF100": {
            "RTNCD": "00",
            "IT_TAB": [
                {
                    "MTORDERNO": "order",
                    "PAYMENTDATE": "B",
                    "PREV_INDCT": "100",
                    "METERDATE": "20260807",
                    "NREVB_INDCT": "0",
                    "METERDATE_CM": "",
                }
            ],
        },
        "SELF101": {},
    }

    async def request(session, code, body):
        calls.append((code, body))
        result = responses[code]
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(haeyang, "request", request)
    monkeypatch.setattr(haeyang, "today", lambda: date(2026, 9, 7))
    client = haeyang.HaeyangClient(
        None,
        {"username": "example", "password": "private"},
        SimpleNamespace(id="haeyang", family="haeyang"),
    )
    return client, responses, calls


async def test_web_login_contract_and_billing_units(wire):
    client, _, calls = wire
    (contract,) = await client.contracts()
    body = calls[0][1]
    assert calls[0][0] == "LOGIN"
    assert body["os_type"] == "mobileweb"
    assert body["legacy_message"]["header"]["info_text"] == "WEB"
    auth = body["legacy_message"]["body"]
    assert auth["userPwdAsMD5"] == haeyang.hashlib.md5(b"private").hexdigest()
    assert "private" not in str(body)
    assert contract.private["install"] == "456"
    bills = await client.bills(contract)
    assert bills["202608"]["amount"] == "12345"
    assert bills["202608"]["reported_usage"] == "9.2"
    assert bills["202608"]["period_start"] == "2026-07-10"
    assert bills["202608"]["closing_reading"] == "100"


async def test_detail_failure_preserves_monthly_bill(wire):
    client, responses, _ = wire
    (contract,) = await client.contracts()
    responses["BILL002"] = GasError("provider_schema_changed")
    assert (await client.bills(contract))["202608"]["amount"] == "12345"
    assert client.history_errors == {contract.key: ["provider_schema_changed"]}


async def test_bills_report_progress_like_every_other_adapter(wire):
    client, _, _ = wire
    (contract,) = await client.contracts()
    steps = []
    bills = await client.bills(
        contract, {}, progress=lambda b, done, total: steps.append((sorted(b), done, total))
    )
    assert steps == [(["202608"], 1, 1)]
    assert client.history_errors == {contract.key: []}
    assert "202608" in bills


@pytest.mark.parametrize("failure", ["unavailable", "invalid_detail"])
async def test_detail_failure_retains_cached_detail_for_unchanged_summary(wire, failure):
    client, responses, _ = wire
    (contract,) = await client.contracts()
    cached = await client.bills(contract)
    original = deepcopy(cached)
    if failure == "unavailable":
        responses["BILL002"] = GasError("cannot_connect")
    else:
        # A malformed late field must not replace only part of the saved detail.
        responses["BILL002"]["IT_TAB"][0].update(USE_PERIOD_FROM="20260711", CURR_INDCT="invalid")
    result = await client.bills(contract, cached)
    assert result == original
    assert cached == original
    assert client.history_errors[contract.key]


async def test_failed_detail_does_not_attach_old_detail_to_changed_summary(wire):
    client, responses, _ = wire
    (contract,) = await client.contracts()
    cached = await client.bills(contract)
    responses["BILL001"]["IT_TAB"][0]["CONSUME_QTY"] = "10"
    responses["BILL002"] = GasError("cannot_connect")
    bill = (await client.bills(contract, cached))["202608"]
    assert bill["reported_usage"] == "10"
    assert bill["closing_reading"] is None
    assert bill["period_start"] is None


async def test_duplicate_bill_month_fails_before_publishing_partial_history(wire):
    client, responses, calls = wire
    (contract,) = await client.contracts()
    cached = await client.bills(contract)
    original = deepcopy(cached)
    responses["BILL001"]["IT_TAB"].append(
        {**responses["BILL001"]["IT_TAB"][0], "NOTICE_AMT": "999"}
    )
    calls.clear()
    progress = []
    with pytest.raises(GasError, match="duplicate_bill_month"):
        await client.bills(contract, cached, progress=lambda *args: progress.append(args))
    assert cached == original
    assert not progress
    assert not any(code == "BILL002" for code, _ in calls)


async def test_preflight_read_failure_does_not_lock_unattempted_submission(wire):
    client, responses, calls = wire
    (contract,) = await client.contracts()
    now = datetime(2026, 9, 7, tzinfo=timezone.utc)
    expected = await client.meter(contract)

    async def write(window, value):
        await client.submit(contract, window, value, now=now)

    manager = SubmissionManager({}, AsyncMock(), AsyncMock(return_value=expected), write)
    proposal = manager.proposal("105", "manual", now, expected)
    good = responses["SELF100"]
    responses["SELF100"] = GasError("cannot_connect")
    with pytest.raises(GasError, match="^cannot_connect$"):
        await manager.submit(proposal["id"], now, lambda *args: None)
    assert manager.state[expected.cycle]["status"] == "not_sent"
    assert "last_attempt_day" not in manager.state[expected.cycle]
    assert not any(code == "SELF101" for code, _ in calls)

    responses["SELF100"] = good
    # Recovery still requires an explicit submit and a matching receipt.
    receipt = deepcopy(expected)
    receipt.submitted = "105"
    manager.query = AsyncMock(side_effect=[expected, receipt])
    assert (await manager.submit(proposal["id"], now, lambda *args: None))["status"] == "confirmed"
    assert sum(code == "SELF101" for code, _ in calls) == 1


async def test_exact_write_dto_no_payer_or_fake_image(wire):
    client, _, calls = wire
    (contract,) = await client.contracts()
    expected = await client.meter(contract)
    assert expected.start == "2026-09-06" and expected.end == "2026-09-10"
    await client.submit(contract, expected, "105", now=datetime(2026, 9, 7, tzinfo=timezone.utc))
    assert calls[-1] == ("SELF101", {"CURR_INDCT": "105", "MTORDERNO": "order", "IFFLAG": "W"})
    assert sum(code == "SELF101" for code, _ in calls) == 1


@pytest.mark.parametrize("change", ["order", "closed", "receipt", "unknown_receipt"])
async def test_changed_or_already_written_meter_never_posts(wire, change):
    client, responses, calls = wire
    (contract,) = await client.contracts()
    expected = await client.meter(contract)
    row = responses["SELF100"]["IT_TAB"][0]
    if change == "order":
        row["MTORDERNO"] = "new"
    elif change == "closed":
        responses["SELF100"]["RTNCD"] = "01"
    elif change == "receipt":
        row.update(METERDATE_CM="20260907", NREVB_INDCT="105")
    else:
        row["NREVB_INDCT"] = "105"
    with pytest.raises(SubmissionNotSent):
        await client.submit(
            contract, expected, "105", now=datetime(2026, 9, 7, tzinfo=timezone.utc)
        )
    assert not any(code == "SELF101" for code, _ in calls)


async def test_write_timeout_uncertain_once(wire):
    client, responses, calls = wire
    (contract,) = await client.contracts()
    expected = await client.meter(contract)
    responses["SELF101"] = GasError("cannot_connect")
    with pytest.raises(SubmissionUncertain):
        await client.submit(
            contract, expected, "105", now=datetime(2026, 9, 7, tzinfo=timezone.utc)
        )
    assert sum(code == "SELF101" for code, _ in calls) == 1


async def test_end_month_leap_year_window(wire, monkeypatch):
    client, responses, _ = wire
    (contract,) = await client.contracts()
    monkeypatch.setattr(haeyang, "today", lambda: date(2028, 2, 28))
    responses["SELF100"]["IT_TAB"][0].update(PAYMENTDATE="S", METERDATE="20280131")
    meter = await client.meter(contract)
    assert (meter.start, meter.end) == ("2028-02-28", "2028-02-29")


def test_response_web_plaintext_and_auth_classification():
    assert haeyang.decode(haeyang.envelope("SELF100", {"RTNCD": "00"})) == {"RTNCD": "00"}
    with pytest.raises(AuthenticationError, match="reauth_required"):
        haeyang.decode({"header": {"result": False, "error_code": "ERR000"}})
    with pytest.raises(AuthenticationError, match="invalid_auth"):
        haeyang.decode({"header": {"result": False, "error_code": "LOGIN010001"}})
    with pytest.raises(GasError, match="provider_schema_changed"):
        haeyang.decode(haeyang.envelope("SELF100", "unverified-ciphertext"))


async def test_transport_posts_encoded_message_and_does_not_follow_redirects():
    class Content:
        async def iter_chunked(self, size):
            yield b'{"header":{"result":true},"body":{"IT_TAB":[]}}'

    class Response:
        status = 200
        content = Content()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    calls = []

    class Session:
        def post(self, url, **kwargs):
            calls.append((url, kwargs))
            return Response()

    assert await haeyang.request(Session(), "SELF100", {"PAYERNO": "123"}) == {"IT_TAB": []}
    assert len(calls) == 1
    url, kwargs = calls[0]
    assert url == "https://m.hyenergy.co.kr/bizmob/SELF100.json"
    assert kwargs["allow_redirects"] is False
    assert "json" not in kwargs
    payload = haeyang.json.loads(kwargs["data"]["message"])
    assert payload["header"]["info_text"] == "WEB"
    assert payload["body"] == {"PAYERNO": "123"}
    with pytest.raises(GasError, match="provider_endpoint_not_allowed"):
        await haeyang.request(Session(), "SELF201", {})
    assert len(calls) == 1


async def test_stale_order_cycle_stable_across_month_rollover(wire, monkeypatch):
    client, _, _ = wire
    (contract,) = await client.contracts()
    first = await client.meter(contract)
    monkeypatch.setattr(haeyang, "today", lambda: date(2026, 10, 7))
    stale = await client.meter(contract)
    assert stale.cycle == first.cycle
    assert not stale.is_open(date(2026, 10, 7))
    with pytest.raises(SubmissionNotSent):
        await client.submit(contract, stale, "105", now=datetime(2026, 10, 7, tzinfo=timezone.utc))


async def test_missing_previous_date_order_cycle_still_stable(wire, monkeypatch):
    client, responses, _ = wire
    (contract,) = await client.contracts()
    responses["SELF100"]["IT_TAB"][0].pop("METERDATE")
    first = await client.meter(contract)
    monkeypatch.setattr(haeyang, "today", lambda: date(2026, 10, 7))
    second = await client.meter(contract)
    assert first.cycle == second.cycle
    assert first.start != second.start
