"""Exercise the actual HA close phase used by ESPHome socket cleanup."""

from copy import deepcopy
from datetime import timedelta
from unittest.mock import patch

import pytest
from homeassistant.const import EVENT_HOMEASSISTANT_CLOSE
from homeassistant.core import CoreState, callback
from test_ha import CONTRACT, entry
from test_sensorless import NOW

from custom_components.busan_city_gas.coordinator import AccountCoordinator


@pytest.mark.parametrize("elapsed", [30, 121])
async def test_close_disconnect_preserves_checkpoint(hass, elapsed):
    with patch(
        "custom_components.busan_city_gas.coordinator.dt_util.now", return_value=NOW
    ) as clock:
        hass.states.async_set("sensor.gas_meter", "100")
        runtime = AccountCoordinator(hass, entry({"source_entity": "sensor.gas_meter"}))
        await runtime.initialize()
        await runtime.calibrate(CONTRACT.key, "35")
        expected = deepcopy(runtime.estimates[CONTRACT.key].dump())
        observed = []

        @callback
        def disconnect_on_close(event):
            observed.append((hass.state, hass.is_stopping))
            hass.states.async_set("sensor.gas_meter", "unavailable")

        remove = hass.bus.async_listen(EVENT_HOMEASSISTANT_CLOSE, disconnect_on_close)
        try:
            hass.set_state(CoreState.running)
            clock.return_value = NOW + timedelta(seconds=elapsed)
            await hass.async_stop()
            await hass.async_block_till_done()
            assert observed == [(CoreState.not_running, False)]
            # Drain any delayed Store write queued after FINAL_WRITE, without
            # sleeping. This is real persistence, not the mocked save fixture.
            await runtime.store._async_handle_write_data()
            saved = await runtime.store.async_load()
            assert saved["contracts"][CONTRACT.key]["estimate"] == expected
            assert runtime.estimates[CONTRACT.key].dump() == expected
            assert not runtime.startup_deadlines
        finally:
            remove()
            await runtime.shutdown()
