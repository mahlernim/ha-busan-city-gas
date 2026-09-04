"""Offline EnergyTalk public-wire fixtures; no real accounts or writes."""

from datetime import datetime
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from custom_components.busan_city_gas.energytalk import EnergyTalkClient, envelope, volume
from custom_components.busan_city_gas.model import GasError
from custom_components.busan_city_gas.portal import (
    AuthenticationError,
    ConnectionError,
    Contract,
    opaque,
)
from custom_components.busan_city_gas.provider import Provider
from custom_components.busan_city_gas.submission_transport import (
    SubmissionNotSent,
    SubmissionRejected,
    SubmissionUncertain,
)


def setup(tenant="cncity"):
    provider = Provider(tenant, "Test", tenant, tenant, family="energytalk")
    client = EnergyTalkClient(None, {"energytalk_token": "synthetic-token"}, provider)
    key = opaque(f"energytalk:{tenant}:1234")
    return client, Contract(
        key,
        key,
        "1234",
        "Home",
        {"tenant": tenant, "custNo": "1234", "address": "synthetic address"},
    )


def info(tenant="cncity"):
    return {
        "responseCode": "ok",
        "clientId": tenant,
        "address": "synthetic address",
        "custNo": "1234",
    }


def meter(**changes):
    return {
        "responseCode": "ok",
        "checkYn": "Y",
        "meterNumber": "serial",
        "prevGuideline": "100 m³",
        **changes,
    }


@pytest.mark.parametrize(
    "tenant",
    [
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
    ],
)
async def test_all_tenants_return_only_token_selected_contract(tenant):
    client, expected = setup(tenant)
    client.call = AsyncMock(
        side_effect=[
            info(tenant),
            {
                "responseCode": "ok",
                "list": [
                    {"custNo": "other", "address": "different"},
                    {"custNo": "1234", "address": "synthetic address"},
                ],
            },
        ]
    )
    result = await client.contracts()
    assert len(result) == 1
    assert result[0].key == expected.key
    assert result[0].private == expected.private


async def test_contracts_do_not_guess_when_no_address_selected():
    client, _ = setup()
    client.call = AsyncMock(return_value={"responseCode": "ok", "clientId": "cncity"})
    with pytest.raises(GasError, match="energytalk_address_required"):
        await client.contracts()


async def test_wrong_tenant_never_queries_meter():
    client, contract = setup()
    client.call = AsyncMock(return_value=info("kne"))
    with pytest.raises(GasError, match="wrong_account"):
        await client.meter(contract)
    assert client.call.await_count == 1


async def test_changed_selected_address_does_not_read_other_contract():
    client, contract = setup()
    client.call = AsyncMock(return_value={**info(), "address": "other"})
    with pytest.raises(GasError, match="wrong_account"):
        await client.bills(contract, {})
    assert client.call.await_count == 1


async def test_bills_use_monthly_usage_not_payment_transactions():
    client, contract = setup()
    client.call = AsyncMock(
        side_effect=[
            info(),
            {
                "responseCode": "ok",
                "list": [
                    {"dateVal": "202609", "amount": "12,345", "usageVal": "12.5㎥"},
                    {"dateVal": "202608", "amount": "500", "usageVal": "20 MJ"},
                ],
            },
        ]
    )
    result = await client.bills(contract, {})
    assert result["202609"]["amount"] == "12345"
    assert result["202609"]["reported_usage"] == "12.5"
    assert result["202608"]["reported_usage"] is None
    assert client.history_errors[contract.key] == ["provider_usage_unit_unknown"]


async def test_current_permission_is_not_a_forecast_schedule():
    client, contract = setup()
    client.call = AsyncMock(side_effect=[info(), meter()])
    result = await client.meter(contract)
    assert result.private["dynamic_window"] is True
    assert result.start == result.end == datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat()
    assert result.planned is None
    assert result.eligible


@pytest.mark.parametrize(
    "changes", [{"checkYn": "N"}, {"meterNumber": None}, {"prevGuideline": None}]
)
async def test_missing_meter_fields_are_readable_and_ineligible(changes):
    client, contract = setup()
    client.call = AsyncMock(side_effect=[info(), meter(**changes)])
    assert not (await client.meter(contract)).eligible


async def test_confirmed_value_only_comes_from_meter_readback():
    client, contract = setup()
    client.call = AsyncMock(side_effect=[info(), meter(recentGuideLine="123")])
    result = await client.meter(contract)
    assert result.submitted == "123"
    assert result.private["submission_blocked"] is True


async def test_single_multipart_write_after_server_value_check():
    client, contract = setup()
    client.call = AsyncMock(side_effect=[info(), meter()])
    expected = await client.meter(contract)
    client.call = AsyncMock(side_effect=[info(), meter(), {"responseCode": "ok", "addableYn": "Y"}])
    client.post_reading = AsyncMock(return_value={"responseCode": "ok"})
    assert (
        await client.submit(contract, expected, 123, now=datetime.now(ZoneInfo("Asia/Seoul")))
        is None
    )
    client.post_reading.assert_awaited_once_with("123")
    assert client.call.await_args.args == (
        "POST",
        "/gas/api/self-meter/check",
        {"guideline": "123"},
    )


async def test_failed_precheck_never_calls_mutation():
    client, contract = setup()
    client.call = AsyncMock(side_effect=[info(), meter()])
    expected = await client.meter(contract)
    client.call = AsyncMock(side_effect=[info(), meter(), {"responseCode": "ok", "addableYn": "N"}])
    client.post_reading = AsyncMock()
    with pytest.raises(SubmissionNotSent):
        await client.submit(contract, expected, 123, now=datetime.now(ZoneInfo("Asia/Seoul")))
    client.post_reading.assert_not_awaited()


@pytest.mark.parametrize(
    "outcome,error",
    [
        (ConnectionError("lost"), SubmissionUncertain),
        ({"responseCode": "fail"}, SubmissionRejected),
    ],
)
async def test_write_failure_never_retries(outcome, error):
    client, contract = setup()
    client.call = AsyncMock(side_effect=[info(), meter()])
    expected = await client.meter(contract)
    client.call = AsyncMock(side_effect=[info(), meter(), {"responseCode": "ok", "addableYn": "Y"}])
    client.post_reading = AsyncMock(side_effect=[outcome])
    with pytest.raises(error):
        await client.submit(contract, expected, 123, now=datetime.now(ZoneInfo("Asia/Seoul")))
    assert client.post_reading.await_count == 1


@pytest.mark.parametrize("code", ["no-token", "expired-token", "invalid-token"])
def test_explicit_auth_envelopes(code):
    with pytest.raises(AuthenticationError, match="reauth_required"):
        envelope({"responseCode": code, "responseMsg": "private details"})


@pytest.mark.parametrize("code", [None, "unexpected", "maintenance", "error"])
def test_unknown_envelope_never_counts_as_success(code):
    with pytest.raises(GasError):
        envelope({"responseCode": code})


@pytest.mark.parametrize("value", ["12 MJ", "12 m³ then 44", "NaN", "1,23 m³"])
def test_volume_requires_unambiguous_units(value):
    with pytest.raises(GasError):
        volume({"value": value}, "value")


async def test_no_other_endpoints_are_callable():
    client, _ = setup()
    with pytest.raises(GasError, match="unsupported_operation"):
        await client.call("POST", "/gas/api/auth/token/kakao", {})


async def test_proxy_transport_uses_observed_envelope(monkeypatch):
    client, _ = setup()
    transport = AsyncMock(return_value=info())
    monkeypatch.setattr("custom_components.busan_city_gas.energytalk.request", transport)
    await client.identity()
    transport.assert_awaited_once()
    assert transport.await_args.args[1:] == ("POST", "https://energytalk.ai/api/fetch")
    assert transport.await_args.kwargs["body"] == {
        "method": "GET",
        "url": "/gas/api/user/info",
        "body": {},
    }
    assert transport.await_args.kwargs["headers"]["Authorization"] == "Bearer synthetic-token"


class Response:
    status = 200

    def __init__(self):
        self.content = self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def iter_chunked(self, size):
        yield b'{"responseCode":"ok"}'


async def test_real_multipart_shape():
    from unittest.mock import Mock

    client, _ = setup()
    client.session = Mock()
    client.session.request.return_value = Response()
    assert await client.post_reading("123") == {"responseCode": "ok"}
    client.session.request.assert_called_once()
    args, kwargs = client.session.request.call_args
    assert args == ("POST", "https://energytalk.ai/api/formdata")
    assert kwargs["allow_redirects"] is False
    assert kwargs["headers"]["X-Backend-Method"] == "POST"
    assert kwargs["headers"]["X-Backend-Url"] == "%2Fgas%2Fapi%2Fself-meter"
    assert kwargs["data"].is_multipart
    assert kwargs["data"]._fields[0][0]["name"] == "guideline"
    assert kwargs["data"]._fields[0][2] == "123"


@pytest.mark.parametrize("token", ["", "Bearer abc", "abc\r\nInjected", " leading"])
def test_token_header_rejects_control_or_whitespace(token):
    provider = Provider("cncity", "Test", "cncity", "cncity", family="energytalk")
    with pytest.raises(AuthenticationError):
        EnergyTalkClient(None, {"energytalk_token": token}, provider)


async def test_cycle_identity_canonical_baseline_not_calendar_or_receipt():
    client, contract = setup()
    client.call = AsyncMock(
        side_effect=[
            info(),
            meter(prevGuideline="100.0"),
            info(),
            meter(prevGuideline="100.00", recentGuideLine="123"),
            info(),
            meter(prevGuideline="100"),
            info(),
            meter(prevGuideline="123"),
        ]
    )
    first = await client.meter(contract)
    confirmed = await client.meter(contract)
    no_receipt = await client.meter(contract)
    rollover = await client.meter(contract)
    assert first.cycle == confirmed.cycle == no_receipt.cycle
    assert rollover.cycle != first.cycle
    assert first.start == first.end  # Permission snapshot stays separate.


@pytest.mark.parametrize("confirmed", [False, True], ids=["uncertain", "confirmed"])
async def test_midnight_and_missing_receipt_do_not_allow_another_write(confirmed):
    from copy import deepcopy

    from custom_components.busan_city_gas.submission import SubmissionManager

    client, contract = setup()
    client.call = AsyncMock(side_effect=[info(), meter()])
    first = await client.meter(contract)
    first.start = first.end = "2026-09-30"
    tomorrow = deepcopy(first)
    tomorrow.start = tomorrow.end = "2026-10-01"
    before = datetime(2026, 9, 30, 23, 55, tzinfo=ZoneInfo("Asia/Seoul"))
    after = datetime(2026, 10, 1, 0, 5, tzinfo=ZoneInfo("Asia/Seoul"))
    receipt = deepcopy(first)
    receipt.submitted = "123" if confirmed else None
    current = [first]
    reads = 0

    async def query():
        nonlocal reads
        reads += 1
        return receipt if reads == 2 else current[0]

    write = (
        AsyncMock(return_value=None)
        if confirmed
        else AsyncMock(side_effect=SubmissionUncertain("submission_uncertain"))
    )
    state = {}
    manager = SubmissionManager(state, AsyncMock(), query, write)
    initial = manager.proposal("123", "manual", before, first)
    if confirmed:
        await manager.submit(initial["id"], before, lambda *_: None)
    else:
        with pytest.raises(GasError, match="submission_uncertain"):
            await manager.submit(initial["id"], before, lambda *_: None)
    assert write.await_count == 1
    assert state[first.cycle]["status"] == ("confirmed" if confirmed else "uncertain")
    # Next month's billing date and a disappearing receipt cannot unlock a POST.
    current[0] = tomorrow
    next_proposal = manager.proposal("123", "manual", after, tomorrow)
    if confirmed:
        assert (await manager.submit(next_proposal["id"], after, lambda *_: None))[
            "status"
        ] == "confirmed"
    else:
        with pytest.raises(GasError, match="submission_uncertain"):
            await manager.submit(next_proposal["id"], after, lambda *_: None)
    assert write.await_count == 1
    assert len(state) == 1
