"""Offline HA lifecycle and nullable-data regressions for additional providers."""

from dataclasses import asdict
from datetime import timedelta
from types import MappingProxyType
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntry
from homeassistant.util import dt as dt_util

from custom_components.busan_city_gas.config_flow import GasConfigFlow
from custom_components.busan_city_gas.const import DEFAULT_OPTIONS, DOMAIN
from custom_components.busan_city_gas.coordinator import AccountCoordinator
from custom_components.busan_city_gas.diagnostics import async_get_config_entry_diagnostics
from custom_components.busan_city_gas.model import Bill, Estimate, MeterWindow, forecast
from custom_components.busan_city_gas.portal import Contract, opaque
from custom_components.busan_city_gas.provider import PROVIDERS
from custom_components.busan_city_gas.sensor import DESCRIPTIONS, GasSensor

IDENTITY = {
    "name": "테스트",
    "phone": "01012345678",
    "birthday": "900101",
    "gender": "1",
    "carrier": "1",
}
CREDS = {
    "gasapp_token": "private-token",
    "gasapp_member": "private-member",
    "gasapp_device_id": "test-device",
}
CONTRACT = Contract(
    opaque("gasapp:1:123:456"),
    "private-member",
    "456",
    "계약 테스트",
    {"company": "1", "customerNum": "123", "useContractNum": "456"},
)


def entry(provider_id="seoul", options=None):
    return ConfigEntry(
        version=2,
        minor_version=1,
        domain=DOMAIN,
        title="Test",
        source="user",
        data={
            **CREDS,
            "provider_id": provider_id,
            "contracts": [asdict(CONTRACT)],
            "username": "tester",
            "password": "not-real",
        },
        options={"contracts": {CONTRACT.key: {**DEFAULT_OPTIONS, **(options or {})}}},
        unique_id="test-account",
        discovery_keys=MappingProxyType({}),
        subentries_data=None,
    )


async def test_gasapp_onboarding_routes_sms_then_persists_only_session(hass):
    flow = GasConfigFlow()
    flow.hass, flow.context = hass, {"source": "user"}
    result = await flow.async_step_user({"provider_id": "seoul"})
    assert result["step_id"] == "gasapp_identity"
    with patch(
        "custom_components.busan_city_gas.config_flow.GasappClient.terms",
        AsyncMock(return_value=[{"category": "terms", "text": "required terms"}]),
    ):
        result = await flow.async_step_gasapp_identity(IDENTITY)
    assert result["step_id"] == "gasapp_terms"
    with patch(
        "custom_components.busan_city_gas.config_flow.GasappClient.request_sms",
        AsyncMock(return_value={"requestNo": "test", "responseUniqId": "test"}),
    ) as sms:
        result = await flow.async_step_gasapp_terms({"consent": True})
        assert sms.call_args.args[-1] is True
    assert result["step_id"] == "gasapp_sms"
    with (
        patch(
            "custom_components.busan_city_gas.config_flow.GasappClient.confirm_sms",
            AsyncMock(return_value=CREDS),
        ),
        patch(
            "custom_components.busan_city_gas.gasapp.GasappClient.contracts",
            AsyncMock(return_value=[CONTRACT]),
        ),
    ):
        result = await flow.async_step_gasapp_sms({"otp": "123456"})
        await flow.login_task
        await flow.async_step_login()
        result = await flow.async_step_login_result()
    assert result["step_id"] == "source"  # No fake tariff question for Gasapp.
    assert not flow.gasapp_identity and not flow.gasapp_challenge
    await flow.async_step_source({})
    await flow.async_step_anchor({})
    await flow.async_step_notifications({})
    await flow.async_step_policy({"automatic_submission": True})
    result = await flow.async_step_summary({})
    assert result["data"]["gasapp_token"] == "private-token"
    assert result["options"]["contracts"][CONTRACT.key]["automatic_submission_confirmed"]
    assert all(k not in result["data"] for k in ("otp", "phone", "birthday", "name", "ci", "di"))


async def test_gasapp_reauth_does_not_accept_another_contract(hass):
    flow = GasConfigFlow()
    flow.hass, flow.context = hass, {"source": "reauth"}
    flow.provider, flow.reauth_entry, flow.credentials = PROVIDERS["seoul"], entry(), CREDS
    flow.contracts = [Contract("different", "private-member", "999", "Other")]
    result = await flow.async_step_login_result()
    assert result["reason"] == "wrong_account"


async def test_gasapp_reauth_preserves_contracts_and_options(hass):
    flow = GasConfigFlow()
    flow.hass, flow.context = hass, {"source": "reauth"}
    flow.provider, flow.reauth_entry, flow.credentials = PROVIDERS["seoul"], entry(), CREDS
    flow.contracts = [CONTRACT]
    with patch.object(
        flow, "async_update_reload_and_abort", return_value={"type": "abort"}
    ) as finish:
        await flow.async_step_login_result()
    assert finish.call_args.kwargs == {"data_updates": {**CREDS, "contracts": [asdict(CONTRACT)]}}


async def test_partial_gasapp_bills_and_unregistered_meter_do_not_break_entities(hass):
    runtime = AccountCoordinator(hass, entry())
    runtime.store.async_load = AsyncMock(return_value=None)
    runtime.store.async_save = AsyncMock()
    await runtime.initialize()
    try:
        runtime.client.meter = AsyncMock(
            return_value=MeterWindow("", "", "", "", private={"registered": False})
        )
        runtime.client.bills = AsyncMock(
            return_value={"202608": Bill("202608", None, [], base_charge=None).dump()}
        )
        await runtime._async_update_data()
        view = runtime.view(CONTRACT.key)
        assert view["service_registration_required"]
        assert view["window_status"] == "unknown"
        assert view["window_start"] is None and view["window_end"] is None
        assert view["billed_amount"] is None and view["billed_usage"] is None
        assert view["bills"][0]["start"] is None
        for description in DESCRIPTIONS:
            sensor = GasSensor(runtime, CONTRACT.key, description)
            sensor.native_value  # No Decimal('None') or date.fromisoformat('').
        assert runtime.submissions[CONTRACT.key].enabled
        assert "error" not in runtime.saved["contracts"][CONTRACT.key]
    finally:
        await runtime.shutdown()


async def test_empty_history_is_successful_and_diagnostics_omit_account_values(hass):
    item = entry()
    runtime = AccountCoordinator(hass, item)
    runtime.store.async_load, runtime.store.async_save = AsyncMock(return_value=None), AsyncMock()
    await runtime.initialize()
    try:
        runtime.client.meter = AsyncMock(return_value=MeterWindow("", "", "", ""))
        runtime.client.bills = AsyncMock(return_value={})
        await runtime._async_update_data()
        hass.data[DOMAIN] = {item.entry_id: runtime}
        report = await async_get_config_entry_diagnostics(hass, item)
        assert report["contracts"][0]["bill_count"] == 0
        assert not any(
            secret in str(report)
            for secret in ("private-token", "private-member", CONTRACT.key, "456")
        )
    finally:
        await runtime.shutdown()


def test_partial_bill_never_invents_amount_heat_or_dates():
    bill = Bill("202608", None, [], base_charge=None, reported_usage="12")
    assert bill.usage == 12 and bill.heat is None and bill.start is None and bill.end is None
    assert Bill.load(bill.dump()) == bill
    result = forecast(Estimate(), [bill], MeterWindow("", "", "", ""), False, dt_util.now(), None)
    assert result["accrued_amount"] is None


def test_all_provider_families_offer_writes_without_validation_gate():
    assert len(PROVIDERS) == 30
    assert all(p.supports_submission for p in PROVIDERS.values())
    assert sum(len(p.company_codes) for p in PROVIDERS.values()) == 18


@pytest.mark.parametrize(
    "provider_id", ["seoul", "samchully", "daesung", "daesungclean", "haeyang"]
)
@pytest.mark.parametrize("automatic", [False, True])
async def test_new_provider_manual_and_automatic_submit_share_receipt_state(
    hass, provider_id, automatic
):
    item = entry(
        provider_id,
        {
            "automatic_submission": automatic,
            "automatic_submission_confirmed": automatic,
            "deadline_time": "00:00:00",
        },
    )
    runtime = AccountCoordinator(hass, item)
    assert runtime.provider.id == provider_id
    runtime.store.async_load, runtime.store.async_save = AsyncMock(return_value=None), AsyncMock()
    await runtime.initialize()
    now = dt_util.now()
    window = MeterWindow(
        now.date().isoformat(), now.date().isoformat(), "20", "known-meter", eligible=True
    )
    runtime.client.meter = AsyncMock(return_value=window)

    async def submitted(*args, **kwargs):
        window.submitted = "32"

    runtime.client.submit = AsyncMock(side_effect=submitted)
    try:
        await runtime.check_submission(CONTRACT.key)
        await runtime.calibrate(CONTRACT.key, "32.7")
        proposal = runtime.propose(CONTRACT.key)
        assert proposal["value"] == 32
        if automatic:
            runtime.ready_at = now - timedelta(minutes=5)
            await runtime.tick(now)
            assert runtime.view(CONTRACT.key)["submission_status"] == "confirmed"
            await runtime.tick(now)
        else:
            result = await runtime.submit(CONTRACT.key, proposal["id"])
            assert result["status"] == "confirmed"
            await runtime.submit(CONTRACT.key, proposal["id"])
        runtime.client.submit.assert_awaited_once()
        assert runtime.view(CONTRACT.key)["accepted"] == "32"
    finally:
        await runtime.shutdown()


async def test_missing_meter_identity_does_not_invalidate_calibration(hass):
    runtime = AccountCoordinator(hass, entry())
    runtime.store.async_load, runtime.store.async_save = AsyncMock(return_value=None), AsyncMock()
    await runtime.initialize()
    today = dt_util.now().date().isoformat()
    windows = [
        MeterWindow(today, today, "20", "known"),
        MeterWindow("", "", "", ""),
        MeterWindow(today, today, "20", "replacement"),
    ]
    runtime.client.meter = AsyncMock(side_effect=windows)
    try:
        await runtime.check_submission(CONTRACT.key)
        await runtime.calibrate(CONTRACT.key, "32")
        await runtime.check_submission(CONTRACT.key)
        assert not any(
            r["kind"] == "physical_meter_changed" for r in runtime.estimates[CONTRACT.key].history
        )
        await runtime.check_submission(CONTRACT.key)
        assert runtime.estimates[CONTRACT.key].history[-1]["kind"] == "physical_meter_changed"
    finally:
        await runtime.shutdown()


@pytest.mark.parametrize("provider_id", ["cncity", "gyeongnam", "seorabeol", "gse"])
async def test_energytalk_provider_specific_setup_and_reauth_metadata(hass, provider_id):
    flow = GasConfigFlow()
    flow.hass, flow.context = hass, {"source": "user"}
    result = await flow.async_step_user({"provider_id": provider_id})
    assert result["step_id"] == "energytalk_credentials"
    assert {str(field) for field in result["data_schema"].schema} == {"energytalk_token"}
    with patch(
        "custom_components.busan_city_gas.energytalk.EnergyTalkClient.contracts",
        AsyncMock(return_value=[CONTRACT]),
    ):
        result = await flow.async_step_energytalk_credentials({"energytalk_token": "test-session"})
        assert result["step_id"] == "login"
        await flow.login_task
    assert flow.credentials == {"energytalk_token": "test-session"}
    assert flow.login_error is None
    flow.reauth_entry = entry(provider_id)
    fresh = Contract(
        CONTRACT.key, CONTRACT.bpno, CONTRACT.cano, CONTRACT.label, {"address": "new address"}
    )
    flow.contracts = [fresh]
    with patch.object(
        flow, "async_update_reload_and_abort", return_value={"type": "abort"}
    ) as finish:
        await flow.async_step_login_result()
    updates = finish.call_args.kwargs["data_updates"]
    assert updates["contracts"] == [asdict(fresh)]
    assert "options" not in updates


@pytest.mark.parametrize("provider_id", ["daesung", "daesungclean", "haeyang"])
async def test_additional_password_provider_setup(hass, provider_id):
    flow = GasConfigFlow()
    flow.hass, flow.context = hass, {"source": "user"}
    result = await flow.async_step_user({"provider_id": provider_id})
    assert result["step_id"] == "credentials"
    assert {str(field) for field in result["data_schema"].schema} == {"username", "password"}


async def test_dynamic_permission_hides_deadline_and_does_not_schedule_writes(hass):
    from custom_components.busan_city_gas.config_flow import policy_schema

    item = entry(
        "cncity",
        {
            "automatic_submission": True,
            "automatic_submission_confirmed": True,
            "deadline_time": "00:00:00",
            "reminder_enabled": True,
        },
    )
    item = ConfigEntry(
        version=2,
        minor_version=1,
        domain=DOMAIN,
        title="Test",
        source="user",
        data={**item.data, "energytalk_token": "test-session"},
        options=item.options,
        unique_id="energytalk",
        discovery_keys=MappingProxyType({}),
        subentries_data=None,
    )
    runtime = AccountCoordinator(hass, item)
    runtime.store.async_load, runtime.store.async_save = AsyncMock(return_value=None), AsyncMock()
    await runtime.initialize()
    now = dt_util.now()
    today = now.date().isoformat()
    window = MeterWindow(
        today,
        today,
        "20",
        "known-meter",
        eligible=True,
        private={"dynamic_window": True, "cycle_id": "stable"},
    )
    runtime.client.meter = AsyncMock(return_value=window)
    runtime.client.submit = AsyncMock()
    try:
        await runtime.check_submission(CONTRACT.key)
        await runtime.calibrate(CONTRACT.key, "32")
        runtime.ready_at = now - timedelta(minutes=5)
        await runtime.tick(now)
        runtime.client.submit.assert_not_awaited()
        view = runtime.view(CONTRACT.key)
        assert view["window_start"] is None and view["window_end"] is None
        assert view["window_open"] and not view["automatic_submission"]
        assert "automatic_submission" not in {
            str(k) for k in policy_schema({}, deadline=False).schema
        }
    finally:
        await runtime.shutdown()
