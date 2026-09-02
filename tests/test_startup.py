"""Offline recovery and migration checks. Never write to a utility account."""

from copy import deepcopy
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from test_ha import CONTRACT, entry, make_runtime
from test_sensorless import NOW, history

from custom_components.busan_city_gas.coordinator import AccountCoordinator
from custom_components.busan_city_gas.model import Estimate, GasError, MeterWindow

KEY = CONTRACT.key
SOURCE = "sensor.gas_meter"


async def restored(hass, age=30, gap=False, state=None, attributes=None, saved=None):
    if state is not None:
        hass.states.async_set(SOURCE, state, attributes or {})
    runtime = AccountCoordinator(hass, entry({"source_entity": SOURCE}))
    if saved is None:
        estimate = Estimate(
            value="35",
            actual="35",
            actual_at=(NOW - timedelta(days=1)).isoformat(),
            source_last="100",
            source_at=(NOW - timedelta(seconds=age)).isoformat(),
            gap=gap,
        )
        saved = {
            "contracts": {
                KEY: {
                    "source": SOURCE,
                    "estimate": estimate.dump(),
                    "submissions": {"old": {"status": "confirmed"}},
                }
            }
        }
    runtime.store.async_load = AsyncMock(return_value=deepcopy(saved))
    runtime.store.async_save = AsyncMock()
    await runtime.initialize()
    return runtime


@pytest.mark.parametrize(
    "age,state,attributes,waiting,gap,value",
    [
        (30, None, {}, True, False, "35"),
        (30, "101", {}, False, False, "36"),
        (30, "101", {"restored": True}, True, False, "35"),
        (121, None, {}, False, True, "35"),
        (30, "1", {}, False, True, "36"),
        (-1, None, {}, False, True, "35"),
    ],
)
async def test_startup_states(hass, age, state, attributes, waiting, gap, value):
    with patch("custom_components.busan_city_gas.coordinator.dt_util.now", return_value=NOW):
        runtime = await restored(hass, age=age, state=state, attributes=attributes)
        try:
            view = runtime.view(KEY)
            assert view["source_waiting"] is waiting
            assert view["gap"] is gap
            assert view["reading"] == value
            if waiting:
                assert view["submission_blocked"]
                with pytest.raises(GasError, match="source_waiting"):
                    runtime.validate_submission(KEY, {"value": 35, "origin": "sensor"}, None)
        finally:
            await runtime.shutdown()


async def test_wait_recovers_once_and_day_is_incomplete(hass):
    with patch(
        "custom_components.busan_city_gas.coordinator.dt_util.now", return_value=NOW
    ) as clock:
        runtime = await restored(hass)
        try:
            clock.return_value = NOW + timedelta(seconds=60)
            hass.states.async_set(SOURCE, "100.2")
            runtime.observe(KEY)
            runtime.observe(KEY)
            assert runtime.estimates[KEY].value == "35.2"
            assert not runtime.estimates[KEY].gap
            assert runtime.estimates[KEY].actual_at == (NOW - timedelta(days=1)).isoformat()
            assert runtime.estimates[KEY].days[NOW.date().isoformat()]["invalid"]
            assert not runtime.estimates[KEY].days[NOW.date().isoformat()]["complete"]
        finally:
            await runtime.shutdown()


async def test_restart_does_not_extend_deadline(hass):
    with patch(
        "custom_components.busan_city_gas.coordinator.dt_util.now", return_value=NOW
    ) as clock:
        first = await restored(hass)
        saved = deepcopy(first.dump())
        deadline = first.startup_deadlines[KEY]
        await first.shutdown()
        clock.return_value = NOW + timedelta(seconds=60)
        second = await restored(hass, saved=saved)
        try:
            assert second.startup_deadlines[KEY] == deadline
            clock.return_value = deadline
            second.observe(KEY)
            assert second.estimates[KEY].gap
            assert KEY not in second.startup_deadlines
        finally:
            await second.shutdown()


async def test_existing_gap_never_gets_grace(hass):
    with patch("custom_components.busan_city_gas.coordinator.dt_util.now", return_value=NOW):
        runtime = await restored(hass, gap=True, state="100")
        try:
            assert runtime.estimates[KEY].gap
            assert not runtime.startup_deadlines
        finally:
            await runtime.shutdown()


async def test_sensorless_migration_and_atomic_failed_correction(hass):
    with patch(
        "custom_components.busan_city_gas.coordinator.dt_util.now", return_value=NOW
    ) as clock:
        runtime = await make_runtime(hass)
        try:
            runtime.saved["contracts"][KEY]["bills"] = {b.month: b.dump() for b in history()}
            await runtime.calibrate(KEY, "35")
            before = deepcopy(runtime.dump())
            clock.return_value = NOW + timedelta(days=1)
            with pytest.raises(GasError, match="confirm_large_correction"):
                await runtime.calibrate(KEY, "99")
            assert runtime.dump() == before
            assert float(runtime.view(KEY)["reading"]) == 36
            runtime.windows[KEY] = MeterWindow("2026-09-13", "2026-09-18", "0", "A")
            runtime.sync_model(KEY)
            with pytest.raises(GasError, match="historical_submission_disabled"):
                runtime.validate_submission(
                    KEY, {"value": 36, "origin": "historical"}, runtime.windows[KEY]
                )
        finally:
            await runtime.shutdown()
