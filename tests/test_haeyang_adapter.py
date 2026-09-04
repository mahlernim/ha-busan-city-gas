"""Synthetic Haeyang mobile-web protocol tests; no live customer requests."""

from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from custom_components.busan_city_gas import haeyang
from custom_components.busan_city_gas.model import GasError
from custom_components.busan_city_gas.portal import AuthenticationError
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
    assert client.history_errors == {"202608": "provider_schema_changed"}


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
