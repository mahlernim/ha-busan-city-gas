"""Graceful HA shutdown is not an ordinary in-service measurement failure."""

import pytest
from homeassistant.core import CoreState
from test_ha import CONTRACT, make_runtime


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
