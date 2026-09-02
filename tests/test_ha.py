from dataclasses import asdict
from datetime import timedelta
from types import MappingProxyType, SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.config_entries import ConfigEntry
from homeassistant.util import dt as dt_util

from custom_components.busan_city_gas.config_flow import GasConfigFlow, GasOptionsFlow
from custom_components.busan_city_gas.const import DEFAULT_OPTIONS, DOMAIN
from custom_components.busan_city_gas.coordinator import AccountCoordinator
from custom_components.busan_city_gas.model import GasError, MeterWindow
from custom_components.busan_city_gas.portal import AuthenticationError, Contract
from custom_components.busan_city_gas.sensor import DESCRIPTIONS, GasSensor

CONTRACT = Contract("test-key", "test-bp", "test-ca", "테스트 계약")


async def finish_login(flow):
    result = await flow.async_step_user({"username": "test", "password": "not-real"})
    assert result["type"] == "progress"
    assert "not-real" not in str(result)
    await flow.login_task
    result = await flow.async_step_login()
    assert result["type"] == "progress_done"
    return await flow.async_step_login_result()


def entry(options=None):
    return ConfigEntry(
        version=1,
        minor_version=1,
        domain=DOMAIN,
        title="Test",
        source="user",
        data={"username": "test-user", "password": "test-pass", "contracts": [asdict(CONTRACT)]},
        options={"contracts": {CONTRACT.key: {**DEFAULT_OPTIONS, **(options or {})}}},
        unique_id="test-account",
        discovery_keys=MappingProxyType({}),
        subentries_data=None,
    )


async def test_login_only_one_contract_skips_selection(hass):
    flow = GasConfigFlow()
    flow.hass = hass
    flow.context = {"source": "user"}
    with patch(
        "custom_components.busan_city_gas.config_flow.PortalClient.contracts",
        AsyncMock(return_value=[CONTRACT]),
    ):
        result = await finish_login(flow)
    assert result["step_id"] == "source"
    result = await flow.async_step_source({})
    assert result["step_id"] == "anchor"
    result = await flow.async_step_anchor({})
    assert result["step_id"] == "notifications"
    result = await flow.async_step_notifications({"recipients": [], "weekly_day": "5"})
    assert result["step_id"] == "policy"
    result = await flow.async_step_policy(
        {"automatic_submission": False, "deadline_time": "22:00:00"}
    )
    assert result["step_id"] == "summary"
    assert "not-real" not in str(result)
    result = await flow.async_step_summary({})
    assert result["type"] == "create_entry"
    assert result["options"]["contracts"][CONTRACT.key]["source_entity"] == ""


async def test_multiple_contract_selection(hass):
    flow = GasConfigFlow()
    flow.hass = hass
    flow.context = {"source": "user"}
    second = Contract("second", "test-bp", "test-2", "Second")
    with patch(
        "custom_components.busan_city_gas.config_flow.PortalClient.contracts",
        AsyncMock(return_value=[CONTRACT, second]),
    ):
        result = await finish_login(flow)
    assert result["step_id"] == "contracts"
    result = await flow.async_step_contracts({"contracts": ["second"]})
    assert flow.selected == [second]


async def test_sensorless_onboarding_accepts_optional_physical_anchor(hass):
    flow = GasConfigFlow()
    flow.hass, flow.selected = hass, [CONTRACT]
    flow.position = 0
    await flow.async_step_source()
    result = await flow.async_step_source({})
    assert result["step_id"] == "anchor"
    result = await flow.async_step_anchor({"reading": 35.1})
    assert result["step_id"] == "notifications"
    assert flow.current["initial_estimate"]["actual"] == "35.1"
    assert flow.current["initial_estimate"]["source_last"] is None


@pytest.mark.parametrize(
    "answer,expected", [([], "no_contracts"), (AuthenticationError("invalid_auth"), "invalid_auth")]
)
async def test_login_errors(hass, answer, expected):
    flow = GasConfigFlow()
    flow.hass = hass
    flow.context = {"source": "user"}
    mock = (
        AsyncMock(side_effect=answer)
        if isinstance(answer, Exception)
        else AsyncMock(return_value=answer)
    )
    with patch("custom_components.busan_city_gas.config_flow.PortalClient.contracts", mock):
        result = await finish_login(flow)
    assert result.get("reason", result.get("errors", {}).get("base")) == expected


async def make_runtime(hass, options=None):
    runtime = AccountCoordinator(hass, entry(options))
    runtime.store.async_load = AsyncMock(return_value=None)
    runtime.store.async_save = AsyncMock()
    await runtime.initialize()
    return runtime


async def test_runtime_local_calibration_and_entities(hass):
    hass.states.async_set(
        "sensor.gas_meter", "100", {"unit_of_measurement": "m³", "state_class": "total_increasing"}
    )
    runtime = await make_runtime(hass, {"source_entity": "sensor.gas_meter"})
    try:
        await runtime.calibrate(CONTRACT.key, "35", physical=True)
        sensor = GasSensor(runtime, CONTRACT.key, DESCRIPTIONS[0])
        assert sensor.native_value == 35
        assert sensor.state_class is None
        hass.states.async_set("sensor.gas_meter", "100.02")
        await hass.async_block_till_done()
        assert float(sensor.native_value) == pytest.approx(35.02)
        assert runtime.estimates[CONTRACT.key].actual == "35"
        with pytest.raises(GasError, match="stale_proposal"):
            await runtime.calibrate(CONTRACT.key, "35.1", expected="35")
    finally:
        await runtime.shutdown()


async def test_options_source_policy_forces_historical_off(hass):
    item = entry({"source_entity": "sensor.gas_meter", "allow_historical_submission": True})
    hass.config_entries._entries[item.entry_id] = item
    flow = GasOptionsFlow()
    flow.hass, flow.handler, flow.key = hass, item.entry_id, CONTRACT.key
    result = flow.finish({"deadline_time": "21:00:00"})
    settings = result["data"]["contracts"][CONTRACT.key]
    assert not settings["allow_historical_submission"]
    assert settings["deadline_time"] == "21:00:00"


async def test_authorization_and_no_caller(hass):
    runtime = await make_runtime(hass)
    hass.auth = SimpleNamespace(
        async_get_user=AsyncMock(return_value=SimpleNamespace(is_admin=False, is_active=True))
    )
    try:
        with pytest.raises(GasError, match="not_authorized"):
            await runtime.authorize(CONTRACT.key, None)
        with pytest.raises(GasError, match="not_authorized"):
            await runtime.authorize(CONTRACT.key, "outsider")
        hass.auth.async_get_user.return_value.is_admin = True
        await runtime.authorize(CONTRACT.key, "admin")
    finally:
        await runtime.shutdown()


async def test_deadline_catchup_once_and_query_before_submit(hass):
    runtime = await make_runtime(
        hass,
        {
            "automatic_submission": True,
            "automatic_submission_confirmed": True,
            "deadline_time": "00:00:00",
        },
    )
    now = dt_util.now()
    runtime.ready_at = now - timedelta(minutes=5)
    key = CONTRACT.key
    window = MeterWindow(
        (now.date() - timedelta(days=5)).isoformat(),
        now.date().isoformat(),
        "27",
        "meter",
        eligible=True,
    )
    runtime.windows[key] = window
    runtime.submissions[key].query = AsyncMock(return_value=window)
    runtime.propose = Mock(return_value={"id": "test"})
    runtime.submit = AsyncMock()
    try:
        await runtime.tick(now)
        await runtime.tick(now + timedelta(minutes=1))
        runtime.submissions[key].query.assert_awaited_once()
        runtime.submit.assert_awaited_once_with(key, "test")
    finally:
        await runtime.shutdown()


async def test_already_submitted_suppresses_deadline(hass):
    runtime = await make_runtime(
        hass,
        {
            "automatic_submission": True,
            "automatic_submission_confirmed": True,
            "deadline_time": "00:00:00",
        },
    )
    now = dt_util.now()
    runtime.ready_at = now - timedelta(minutes=5)
    window = MeterWindow(
        (now.date() - timedelta(days=5)).isoformat(),
        now.date().isoformat(),
        "27",
        "meter",
        eligible=True,
        submitted="35",
    )
    runtime.windows[CONTRACT.key] = window
    runtime.submissions[CONTRACT.key].query = AsyncMock(return_value=window)
    runtime.submit = AsyncMock()
    try:
        await runtime.tick(now)
        runtime.submit.assert_not_called()
        assert runtime.view(CONTRACT.key)["submission_status"] == "confirmed"
    finally:
        await runtime.shutdown()
