"""Synthetic Gasapp protocol coverage; no authenticated provider traffic."""

from datetime import datetime
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from custom_components.busan_city_gas.gasapp import GasappClient
from custom_components.busan_city_gas.model import GasError
from custom_components.busan_city_gas.portal import (
    AuthenticationError,
    ConnectionError,
    Contract,
    opaque,
)
from custom_components.busan_city_gas.provider import Provider
from custom_components.busan_city_gas.provider_transport import flag, number, request
from custom_components.busan_city_gas.submission_transport import (
    SubmissionRejected,
    SubmissionUncertain,
)

NOW = datetime(2026, 9, 15, tzinfo=ZoneInfo("Asia/Seoul"))


def client_and_contract():
    provider = Provider("synthetic", "Synthetic", "", "", family="gasapp", company_codes=("1",))
    client = GasappClient(None, {"gasapp_member": "member", "gasapp_token": "private"}, provider)
    contract = Contract(
        opaque("gasapp:1:customer:contract"),
        "member",
        "contract",
        "Home",
        {"company": "1", "customerNum": "customer", "useContractNum": "contract", "amiYn": "N"},
    )
    return client, contract


def target(**updates):
    return {
        "periodStart": "20260913",
        "periodEnd": "20260918",
        "meterIdNum": "serial",
        "lastMonthIndicatorQty": "100",
        "selfInputAvailable": "Y",
        "mtrDigitCnt": "5",
        **updates,
    }


async def test_contracts_are_company_scoped_and_opaque():
    client, contract = client_and_contract()
    client.call = AsyncMock(
        return_value={
            "data": {
                "contracts": [
                    {"company": "1", "customerNum": "customer", "useContractNum": "contract"},
                    {"company": "2", "customerNum": "customer", "useContractNum": "contract"},
                ]
            }
        }
    )
    assert [row.key for row in await client.contracts()] == [contract.key]
    contract.private["company"] = "2"
    with pytest.raises(GasError, match="wrong_account"):
        client.account(contract)


@pytest.mark.parametrize(
    "updates",
    [
        {"periodStart": None},
        {"periodEnd": None},
        {"lastMonthIndicatorQty": None},
        {"selfInputAvailable": "N"},
        {"needChangeRegisteredChannel": "Y"},
    ],
)
async def test_incomplete_meter_is_readable_but_not_eligible(updates):
    client, contract = client_and_contract()
    client.call = AsyncMock(return_value=target(**updates))
    meter = await client.meter(contract)
    assert not meter.is_open(NOW.date())


async def test_unregistered_account_keeps_bills_and_home_history_without_registration():
    client, contract = client_and_contract()
    responses = {
        "bills/summary": {
            "history": [{"requestYm": "202609", "chargeAmt": "1,200", "useQty": "9.1"}]
        },
        "indications": None,
        "meters": [{"meterIdNum": "serial"}],
        "home": {
            "cards": {
                "indication": {
                    "history": [
                        {"jobYmd": "20260901", "thisMonthIndicator": "100", "meterIdNum": "serial"}
                    ]
                }
            }
        },
    }

    async def call(method, path, *args, **kwargs):
        assert method == "GET"
        return responses[path]

    client.call = AsyncMock(side_effect=call)
    bills = await client.bills(contract, {})
    assert bills["202609"]["amount"] == "1200"
    assert bills["202609"]["reported_usage"] == "9.1"
    assert client.readings[contract.key] == [
        {"date": "2026-09-01", "value": "100", "meter": opaque("gasapp:1:serial")}
    ]
    assert "indications/history" not in [c.args[1] for c in client.call.await_args_list]


async def test_optional_history_failure_preserves_bills():
    client, contract = client_and_contract()

    async def call(method, path, *args, **kwargs):
        if path == "bills/summary":
            return [{"requestYm": "202609", "chargeAmt": "1200"}]
        if path == "indications":
            return target()
        raise ConnectionError("unavailable")

    client.call = AsyncMock(side_effect=call)
    assert "202609" in await client.bills(contract, {})
    assert len(client.history_errors[contract.key]) == 2


async def test_auth_expiry_is_not_hidden_as_partial_data():
    client, contract = client_and_contract()
    client.call = AsyncMock(side_effect=[[], AuthenticationError("reauth_required")])
    with pytest.raises(AuthenticationError):
        await client.bills(contract, {})


async def test_history_inclusive_sixth_cursor_and_meter_identity():
    client, contract = client_and_contract()
    entries = [
        {
            "id": str(i),
            "jobYmd": f"202609{i:02}",
            "thisMonthIndicator": str(100 + i),
            "meterIdNum": "serial",
        }
        for i in range(1, 8)
    ]
    client.call = AsyncMock(side_effect=[entries[:6], entries[5:]])
    readings = await client.history(contract)
    assert len(readings) == 7
    assert client.call.await_args.kwargs["params"]["lastId"] == "6"


async def test_history_repeated_cursor_is_bounded():
    client, contract = client_and_contract()
    client.call = AsyncMock(return_value=[{"id": str(i)} for i in range(6)])
    with pytest.raises(GasError, match="history_cursor_repeated"):
        await client.history(contract)
    assert client.call.await_count == 2


async def test_register_requires_consent_and_is_explicit():
    client, contract = client_and_contract()
    client.call = AsyncMock(side_effect=[None, target()])
    with pytest.raises(GasError, match="consent_required"):
        await client.prepare_service(contract, "register", False)
    client.call.assert_not_awaited()
    await client.prepare_service(contract, "register", True)
    assert client.call.await_args_list[0].args[:2] == ("POST", "indications/register")


async def test_submit_sends_one_integer_post_and_never_claims_receipt():
    client, contract = client_and_contract()
    client.call = AsyncMock(return_value=target())
    expected = await client.meter(contract)
    client.call = AsyncMock(side_effect=[target(), {"inputYn": "Y"}])
    assert await client.submit(contract, expected, "123.0", now=NOW) is None
    assert [c.args[:2] for c in client.call.await_args_list] == [
        ("GET", "indications"),
        ("POST", "relay/indications/input"),
    ]
    assert client.call.await_args.kwargs["body"]["thisMonthIndicatorCustomer"] == "123"


@pytest.mark.parametrize(
    "updates",
    [
        {"meterIdNum": "replacement"},
        {"inputYn": "Y"},
        {"thisMonthIndicatorCustomer": "123"},
        {"company": "2"},
    ],
)
async def test_submit_rechecks_identity_and_blocks_existing_reading(updates):
    client, contract = client_and_contract()
    client.call = AsyncMock(return_value=target())
    expected = await client.meter(contract)
    client.call = AsyncMock(return_value=target(**updates))
    with pytest.raises(GasError):
        await client.submit(contract, expected, 123, now=NOW)
    assert all(c.args[0] == "GET" for c in client.call.await_args_list)


@pytest.mark.parametrize(
    "response,error",
    [(ConnectionError("lost"), SubmissionUncertain), ({"inputYn": "N"}, SubmissionRejected)],
)
async def test_submit_failure_has_no_retry(response, error):
    client, contract = client_and_contract()
    client.call = AsyncMock(return_value=target())
    expected = await client.meter(contract)
    client.call = AsyncMock(side_effect=[target(), response])
    with pytest.raises(error):
        await client.submit(contract, expected, 123, now=NOW)
    assert client.call.await_count == 2


@pytest.mark.parametrize("value", ["false", "N", "0", False, None])
def test_negative_flags(value):
    assert flag(value) is False


@pytest.mark.parametrize("value", ["1,23", "nan", "-1", "Infinity", "100000000"])
def test_number_rejects_ambiguous_or_nonfinite_values(value):
    with pytest.raises(GasError):
        number({"value": value}, "value")


class FakeContent:
    def __init__(self, data):
        self.data = data

    async def iter_chunked(self, size):
        for i in range(0, len(self.data), size):
            yield self.data[i : i + size]


class FakeResponse:
    def __init__(self, status, data):
        self.status = status
        self.content = FakeContent(data)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class FakeSession:
    def __init__(self, status=200, data=b"{}", error=None):
        self.status, self.data, self.error = status, data, error
        self.calls = []

    def request(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.error:
            raise self.error
        return FakeResponse(self.status, self.data)


@pytest.mark.parametrize("status", [401, 403, 418])
async def test_transport_auth_expiry_is_redacted(status):
    session = FakeSession(status, b"private payload")
    with pytest.raises(AuthenticationError, match="^reauth_required$"):
        await request(session, "GET", "https://example.invalid")
    assert len(session.calls) == 1


@pytest.mark.parametrize(
    "status,data,code",
    [
        (302, b"", "provider_query_http_302"),
        (200, b"<html>private session</html>", "provider_query_schema_changed"),
        (200, b"x" * 4_000_001, "provider_response_too_large"),
    ],
    ids=["redirect", "non-json", "oversize"],
)
async def test_transport_redirect_and_payload_limits(status, data, code):
    session = FakeSession(status, data)
    with pytest.raises(GasError, match=f"^{code}$"):
        await request(session, "POST", "https://example.invalid", body={"value": "123"})
    assert len(session.calls) == 1
    assert session.calls[0][1]["allow_redirects"] is False


async def test_transport_timeout_never_retries_write():
    session = FakeSession(error=TimeoutError("private server details"))
    with pytest.raises(ConnectionError, match="^provider_meter_connection_failed$"):
        await request(session, "POST", "https://example.invalid", stage="meter")
    assert len(session.calls) == 1
