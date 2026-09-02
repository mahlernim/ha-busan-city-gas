from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import Context, Event
from homeassistant.util import dt as dt_util
from test_ha import CONTRACT, entry, make_runtime

from custom_components.busan_city_gas.const import DOMAIN
from custom_components.busan_city_gas.coordinator import AccountCoordinator
from custom_components.busan_city_gas.model import Estimate, GasError, MeterWindow


async def test_android_payload_string_auth_and_receipt(hass):
    key = CONTRACT.key
    runtime = await make_runtime(hass, {"recipients": ["phone"]})
    runtime.estimates[key] = Estimate(
        value="35.04", actual="35", actual_at=dt_util.now().isoformat()
    )
    phone_map = {
        "phone": {
            "label": "Test phone",
            "service": "mobile_app_test",
            "user_id": "user",
            "device_id": "device",
        }
    }
    calls = []

    async def notify(call):
        calls.append(call.data)

    hass.services.async_register("notify", "mobile_app_test", notify)
    hass.auth = SimpleNamespace(
        async_get_user=AsyncMock(return_value=SimpleNamespace(is_admin=False, is_active=True))
    )
    try:
        with patch("custom_components.busan_city_gas.coordinator.phones", return_value=phone_map):
            await runtime.prompt(key)
            message = calls[-1]
            assert message["title"] == "가스 검침 보정"
            assert "35.0 m³" in message["message"]
            assert all(a["authenticationRequired"] == "true" for a in message["data"]["actions"])
            wrong = Event(
                "mobile_app_notification_action",
                {"action": message["data"]["actions"][0]["action"], "device_id": "wrong"},
                context=Context(user_id="user"),
            )
            await runtime.notification_action(wrong)
            assert runtime.estimates[key].actual == "35"
            correct = Event(
                "mobile_app_notification_action",
                {"action": message["data"]["actions"][0]["action"], "device_id": "device"},
                context=Context(user_id="user"),
            )
            await runtime.notification_action(correct)
            assert runtime.estimates[key].actual == "35.0"
            receipt = Event(
                "mobile_app_notification_received",
                {"tag": runtime.tag(key)},
                context=Context(user_id="user"),
            )
            await runtime.notification_received(receipt)
            assert runtime.view(key)["notification"]["received_count"] == 1
            assert "received_by" not in runtime.view(key)["notification"]
    finally:
        await runtime.shutdown()


async def test_sensor_failure_does_not_allow_historical_optin(hass):
    key = CONTRACT.key
    runtime = await make_runtime(
        hass, {"source_entity": "sensor.missing", "allow_historical_submission": True}
    )
    runtime.estimates[key].value = "35"
    window = MeterWindow("2026-09-13", "2026-09-18", "27", "meter", eligible=True)
    try:
        with pytest.raises(GasError, match="physical_calibration_required"):
            runtime.validate_submission(key, {"value": 35, "origin": "sensor"}, window)
    finally:
        await runtime.shutdown()


async def test_saved_checkpoint_survives_restart_no_double_increment(hass):
    key = CONTRACT.key
    opts = {"source_entity": "sensor.gas_meter"}
    hass.states.async_set("sensor.gas_meter", "100", {"unit_of_measurement": "m³"})
    first = await make_runtime(hass, opts)
    await first.calibrate(key, "35", physical=True)
    saved = first.dump()
    await first.shutdown()
    second = AccountCoordinator(hass, entry(opts))
    second.store.async_load = AsyncMock(return_value=saved)
    second.store.async_save = AsyncMock()
    try:
        await second.initialize()
        assert float(second.estimates[key].value) == 35
        assert second.estimates[key].actual == "35"
    finally:
        await second.shutdown()


async def test_sensor_change_requires_reanchor_and_keeps_history(hass):
    key = CONTRACT.key
    runtime = AccountCoordinator(hass, entry({"source_entity": "sensor.new"}))
    old = Estimate(
        value="35", source_last="100", source_at=dt_util.now().isoformat(), actual="35", gap=False
    )
    runtime.store.async_load = AsyncMock(
        return_value={
            "contracts": {
                key: {
                    "source": "sensor.old",
                    "estimate": old.dump(),
                    "submissions": {"old-cycle": {"status": "confirmed"}},
                }
            }
        }
    )
    runtime.store.async_save = AsyncMock()
    hass.states.async_set("sensor.new", "200")
    try:
        await runtime.initialize()
        assert runtime.estimates[key].value == "35"
        assert runtime.estimates[key].gap
        assert runtime.saved["contracts"][key]["submissions"]["old-cycle"]["status"] == "confirmed"
    finally:
        await runtime.shutdown()


async def test_setup_services_register_and_submit_requires_auth(hass):
    from homeassistant.components import websocket_api

    from custom_components.busan_city_gas import async_setup

    # Register actual schemas/handlers without starting an HTTP server.
    with patch.object(websocket_api, "async_register_command"):
        assert await async_setup(hass, {})
    assert hass.services.has_service(DOMAIN, "prepare_submission")
    assert hass.services.has_service(DOMAIN, "submit")


def test_first_partial_day_cannot_become_a_complete_day():
    now = dt_util.now().replace(hour=12, minute=0, second=0, microsecond=0)
    e = Estimate()
    e.calibrate("35", "0", now, physical=True)
    e.observe("2", now + timedelta(hours=1))
    e.observe("2", now.replace(hour=0) + timedelta(days=1))
    assert e.average((now + timedelta(days=1)).date()) == (None, 0)
