"""Transient transport loss must not discard a valid physical anchor."""

from copy import deepcopy
from datetime import timedelta
from unittest.mock import patch

import pytest
from test_ha import CONTRACT, make_runtime
from test_sensorless import NOW
from test_startup import restored

from custom_components.busan_city_gas.model import GasError

KEY = CONTRACT.key
SOURCE = "sensor.gas_meter"


@pytest.mark.parametrize(
    "elapsed,reading,gap,value",
    [
        (0.4, "100", False, "35"),
        (90, "102", False, "37"),
        (120, "102", True, "37"),
        (1, "1", True, "36"),
    ],
)
async def test_disconnect_recovery(hass, elapsed, reading, gap, value):
    with patch(
        "custom_components.busan_city_gas.coordinator.dt_util.now", return_value=NOW
    ) as clock:
        hass.states.async_set(SOURCE, "100")
        runtime = await make_runtime(hass, {"source_entity": SOURCE})
        try:
            await runtime.calibrate(KEY, "35")
            history = deepcopy(runtime.estimates[KEY].history)
            hass.states.async_set(SOURCE, "unavailable")
            await hass.async_block_till_done()
            assert runtime.view(KEY)["source_waiting"]
            assert runtime.view(KEY)["submission_blocked"]
            with pytest.raises(GasError, match="source_waiting"):
                runtime.validate_submission(KEY, {"value": 35, "origin": "sensor"}, None)
            clock.return_value = NOW + timedelta(seconds=elapsed)
            hass.states.async_set(SOURCE, reading)
            await hass.async_block_till_done()
            runtime.observe(KEY)
            assert runtime.estimates[KEY].value == value
            assert runtime.estimates[KEY].gap is gap
            assert runtime.estimates[KEY].history[: len(history)] == history
            assert runtime.estimates[KEY].actual_at == NOW.isoformat()
            assert not runtime.view(KEY)["source_waiting"]
            assert runtime.estimates[KEY].days[NOW.date().isoformat()]["invalid"]
        finally:
            await runtime.shutdown()


async def test_disconnect_reload_keeps_original_timeout(hass):
    with patch(
        "custom_components.busan_city_gas.coordinator.dt_util.now", return_value=NOW
    ) as clock:
        hass.states.async_set(SOURCE, "100")
        first = await make_runtime(hass, {"source_entity": SOURCE})
        await first.calibrate(KEY, "35")
        hass.states.async_set(SOURCE, "unavailable")
        await hass.async_block_till_done()
        deadline = first.startup_deadlines[KEY]
        clock.return_value = NOW + timedelta(seconds=60)
        first.observe(KEY)
        saved = deepcopy(first.dump())
        await first.shutdown()
        second = await restored(hass, saved=saved)
        try:
            assert second.startup_deadlines[KEY] == deadline
            clock.return_value = deadline
            second.observe(KEY)
            assert second.estimates[KEY].gap
            assert not second.view(KEY)["source_waiting"]
            hass.states.async_set(SOURCE, "100")
            await hass.async_block_till_done()
            assert second.estimates[KEY].gap
        finally:
            await second.shutdown()
