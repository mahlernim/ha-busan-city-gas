"""Synthetic protocol fixtures; no customer endpoint requests."""

from datetime import datetime, timezone

import pytest

from custom_components.busan_city_gas import samchully
from custom_components.busan_city_gas.model import GasError
from custom_components.busan_city_gas.portal import AuthenticationError
from custom_components.busan_city_gas.provider import get_provider
from custom_components.busan_city_gas.submission_transport import (
    SubmissionNotSent,
    SubmissionRejected,
    SubmissionUncertain,
)


@pytest.fixture
def wire(monkeypatch):
    calls = []
    responses = {
        "login-pwd": {"loginToken": "private-login", "userType": "PER"},
        "login": {"accessToken": "private-token"},
        "me": {"userName": "Synthetic", "birthDate": "19900101", "phoneNumber": "010-0000-0000"},
        "custinfo": {"E_TAB": [{"VKONT": "000123", "PHONE": "01000000000"}]},
        "goji-list": {
            "E_TAB": [{"BILLING_PERIOD": "202608", "BETRW_TOT": "12,000", "CONSUMPTION": "9.8"}]
        },
        "meter-check": {"ET_RESULT": [{"KKO_MR_SDATE": "20260901", "KKO_MR_EDATE": "20260930"}]},
        "self-meter": {"E_TIDNR": "meter-123", "E_PRV_M_ZWSTAND": "123"},
        "self-meter-list": {"E_TAB": [{"E_YN": "N", "E_ZWSTAND": "0"}]},
        "validation-tidnr": {"E_RETCD": "S", "E_ZWSTAND": "123"},
        "self-meter-img": {"E_RETCD": "S"},
    }

    async def request(session, method, url, **kwargs):
        path = url.rsplit("/", 1)[-1]
        calls.append((path, kwargs))
        response = responses[path]
        if callable(response):
            response = response()
        if isinstance(response, Exception):
            raise response
        return {"data": response}

    monkeypatch.setattr(samchully, "request", request)
    return (
        samchully.SamchullyClient(
            None, {"username": "test", "password": "secret"}, get_provider("samchully")
        ),
        responses,
        calls,
    )


@pytest.mark.asyncio
async def test_contracts_and_partial_bills(wire):
    client, responses, calls = wire
    (contract,) = await client.contracts()
    assert contract.cano == "000123"
    assert contract.cano not in contract.key
    bills = await client.bills(contract, {})
    bill = bills["202608"]
    assert bill["amount"] == "12000" and bill["reported_usage"] == "9.8"
    assert bill["segments"] == [] and bill["closing_reading"] is None
    assert calls[0][1]["body"]["userPwd"] == "secret"
    assert calls[2][1]["headers"]["X-User-Token"] == "private-token"


@pytest.mark.asyncio
async def test_submission_exact_numeric_fallback_and_two_preflights(wire):
    client, responses, calls = wire
    (contract,) = await client.contracts()
    meter = await client.meter(contract)
    await client.submit(contract, meter, 125, now=datetime(2026, 9, 4, tzinfo=timezone.utc))
    validation = [args["body"] for path, args in calls if path == "validation-tidnr"]
    assert [row["I_GUBUN"] for row in validation] == ["1", "2"]
    assert validation[1]["I_ZWSTAND"] == "125"
    writes = [args for path, args in calls if path == "self-meter-img"]
    assert len(writes) == 1
    assert writes[0]["body"] == {
        "I_VKONT": "000123",
        "I_TIDNR": "meter-123",
        "I_ZWSTAND": "125",
        "I_ZWSTAND_IMG": "125",
        "I_TMP_METER_YN": "N",
    }
    assert "X-User-Token" not in writes[0]["headers"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response,error",
    [
        ({"E_RETCD": "E", "E_RETMG": "private info"}, SubmissionRejected),
        ({"E_RETCD": "?"}, SubmissionUncertain),
        (GasError("provider_submission_connection_failed"), SubmissionUncertain),
    ],
)
async def test_no_write_retry_or_private_error(wire, response, error):
    client, responses, calls = wire
    (contract,) = await client.contracts()
    meter = await client.meter(contract)
    responses["self-meter-img"] = response
    with pytest.raises(error) as exc:
        await client.submit(contract, meter, 125, now=datetime(2026, 9, 4, tzinfo=timezone.utc))
    assert "private" not in str(exc.value)
    assert sum(path == "self-meter-img" for path, _ in calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", ["duplicate", "changed_meter", "submitted", "unknown", "validation", "lower"]
)
async def test_preflight_blocks_ambiguous_or_changed_state(wire, change):
    client, responses, calls = wire
    (contract,) = await client.contracts()
    meter = await client.meter(contract)
    if change == "duplicate":
        responses["custinfo"]["E_TAB"] *= 2
    elif change == "changed_meter":
        responses["self-meter"]["E_TIDNR"] = "replacement"
    elif change in ("submitted", "unknown"):
        responses["self-meter-list"] = {
            "E_TAB": [{"E_YN": "Y" if change == "submitted" else "?", "E_ZWSTAND": "125"}]
        }
    elif change == "validation":
        responses["validation-tidnr"] = {"E_RETCD": "E"}
    with pytest.raises(SubmissionNotSent):
        await client.submit(
            contract,
            meter,
            122 if change == "lower" else 125,
            now=datetime(2026, 9, 4, tzinfo=timezone.utc),
        )
    assert not any(path == "self-meter-img" for path, _ in calls)


@pytest.mark.asyncio
async def test_duplicate_bill_month_rejected(wire):
    client, responses, calls = wire
    (contract,) = await client.contracts()
    responses["goji-list"]["E_TAB"] *= 2
    with pytest.raises(GasError, match="duplicate_bill_month"):
        await client.bills(contract, {})


@pytest.mark.asyncio
async def test_token_renewed_proactively(wire):
    client, responses, calls = wire
    await client.contracts()
    await client.contracts()
    assert sum(path == "login-pwd" for path, _ in calls) == 1
    client.token_expires = 0
    await client.contracts()
    assert sum(path == "login-pwd" for path, _ in calls) == 2


@pytest.mark.asyncio
async def test_read_auth_failure_renews_once(wire):
    client, responses, calls = wire
    user = responses["me"]
    attempts = iter([AuthenticationError("reauth_required"), user])
    responses["me"] = lambda: next(attempts)
    await client.contracts()
    assert sum(path == "login-pwd" for path, _ in calls) == 2
    assert sum(path == "me" for path, _ in calls) == 2


@pytest.mark.asyncio
async def test_persistent_auth_failure_bounded(wire):
    client, responses, calls = wire
    responses["me"] = AuthenticationError("reauth_required")
    with pytest.raises(AuthenticationError):
        await client.contracts()
    assert sum(path == "me" for path, _ in calls) == 2


@pytest.mark.asyncio
async def test_endpoint_allowlist_rejects_before_request(wire):
    client, responses, calls = wire
    with pytest.raises(GasError, match="provider_endpoint_not_allowed"):
        await client._post("https://invalid.example/", {})
    with pytest.raises(GasError, match="provider_endpoint_not_allowed"):
        await client._post("scl/services/self-meter-img", {})
    assert calls == []


def test_credentials_and_provider_validation():
    with pytest.raises(GasError, match="provider_mismatch"):
        samchully.SamchullyClient(None, {"username": "x", "password": "y"}, get_provider("busan"))
    with pytest.raises(AuthenticationError, match="invalid_auth"):
        samchully.SamchullyClient(
            None, {"username": " ", "password": "y"}, get_provider("samchully")
        )
