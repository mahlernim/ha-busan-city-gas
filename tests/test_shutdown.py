"""Graceful HA shutdown is not an ordinary in-service measurement failure."""

from copy import deepcopy
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import CoreState
from test_ha import CONTRACT, entry, make_runtime
from test_sensorless import NOW

from custom_components.busan_city_gas.coordinator import AccountCoordinator


@pytest.mark.parametrize("stop_state", [CoreState.stopping, CoreState.final_write])
async def test_shutdown_disconnect_preserves_last_valid_checkpoint(hass, stop_state):
    hass.states.async_set("sensor.gas_meter", "100")
    runtime = await make_runtime(hass, {"source_entity": "sensor.gas_meter"})
    try:
        await runtime.calibrate(CONTRACT.key, "35")
        checkpoint = runtime.estimates[CONTRACT.key].dump()
        hass.set_state(stop_state)
        hass.states.async_set("sensor.gas_meter", "unavailable")
        await hass.async_block_till_done()
        assert runtime.estimates[CONTRACT.key].dump() == checkpoint
    finally:
        hass.set_state(CoreState.not_running)
        await runtime.shutdown()


async def test_running_disconnect_still_requires_calibration(hass):
    hass.states.async_set("sensor.gas_meter", "100")
    runtime = await make_runtime(hass, {"source_entity": "sensor.gas_meter"})
    try:
        await runtime.calibrate(CONTRACT.key, "35")
        hass.states.async_set("sensor.gas_meter", "unavailable")
        await hass.async_block_till_done()
        assert runtime.estimates[CONTRACT.key].gap
        hass.set_state(CoreState.stopping)
        runtime.observe(CONTRACT.key)
        assert runtime.estimates[CONTRACT.key].gap
    finally:
        hass.set_state(CoreState.not_running)
        await runtime.shutdown()


@pytest.mark.parametrize("elapsed,expected_gap", [(90, False), (121, True)])
async def test_shutdown_saved_state_recovers_only_inside_original_deadline(
    hass, elapsed, expected_gap
):
    key = CONTRACT.key
    opts = {"source_entity": "sensor.gas_meter"}
    with patch(
        "custom_components.busan_city_gas.coordinator.dt_util.now", return_value=NOW
    ) as clock:
        hass.states.async_set("sensor.gas_meter", "100")
        first = await make_runtime(hass, opts)
        await first.calibrate(key, "35")
        actual_at = first.estimates[key].actual_at
        hass.set_state(CoreState.stopping)
        clock.return_value = NOW + timedelta(seconds=30)
        hass.states.async_set("sensor.gas_meter", "unavailable")
        await hass.async_block_till_done()
        saved = deepcopy(first.dump())
        await first.shutdown()
        assert saved["contracts"][key]["estimate"]["source_at"] == NOW.isoformat()
        hass.set_state(CoreState.starting)
        second = AccountCoordinator(hass, entry(opts))
        second.store.async_load = AsyncMock(return_value=saved)
        second.store.async_save = AsyncMock()
        try:
            clock.return_value = NOW + timedelta(seconds=60)
            await second.initialize()
            assert second.view(key)["source_waiting"]
            clock.return_value = NOW + timedelta(seconds=elapsed)
            hass.states.async_set("sensor.gas_meter", "102")
            await hass.async_block_till_done()
            second.observe(key)
            assert second.estimates[key].value == "37"
            assert second.estimates[key].gap is expected_gap
            assert second.estimates[key].actual_at == actual_at
            assert not second.view(key)["source_waiting"]
        finally:
            hass.set_state(CoreState.not_running)
            await second.shutdown()
