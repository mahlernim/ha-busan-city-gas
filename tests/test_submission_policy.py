"""Unlock upgrades do not convert old no-op settings into automatic consent."""

from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from homeassistant.util import dt as dt_util

from custom_components.busan_city_gas.config_flow import GasOptionsFlow, policy_schema
from custom_components.busan_city_gas.const import DEFAULT_OPTIONS
from custom_components.busan_city_gas.model import MeterWindow
from tests.test_ha import CONTRACT, entry, make_runtime


@pytest.mark.parametrize("confirmed", [False, True])
async def test_upgrade_consent_and_deadline(hass, confirmed):
    runtime = await make_runtime(
        hass,
        {
            "automatic_submission": True,
            "automatic_submission_confirmed": confirmed,
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
    runtime.submit = AsyncMock()
    try:
        await runtime.calibrate(key, "35", physical=True)
        view = runtime.view(key)
        assert not view["submission_locked"]
        assert view["automatic_submission"] is confirmed
        assert view["automatic_submission_needs_confirmation"] is not confirmed
        await runtime.tick(now)
        assert runtime.submit.await_count == int(confirmed)
        assert runtime.submissions[key].query.await_count == int(confirmed)
        # Rejected consent must not consume a future scheduler attempt.
        assert ("deadline" in runtime.saved["contracts"][key]["schedule"]) is confirmed
    finally:
        await runtime.shutdown()


@pytest.mark.parametrize("step", ["source", "notifications", "policy"])
async def test_only_explicit_policy_enables_old_setting(hass, step):
    item = entry({"automatic_submission": True})
    hass.config_entries._entries[item.entry_id] = item
    flow = GasOptionsFlow()
    flow.hass, flow.handler, flow.key = hass, item.entry_id, CONTRACT.key
    payload = {"automatic_submission": True} if step == "policy" else {}
    result = await getattr(flow, f"async_step_{step}")(payload)
    settings = result["data"]["contracts"][CONTRACT.key]
    assert settings["automatic_submission_confirmed"] is (step == "policy")
    assert settings["automatic_submission"]


def test_policy_form_defaults_off_for_old_noop_setting():
    values = policy_schema({**DEFAULT_OPTIONS, "automatic_submission": True})({})
    assert not values["automatic_submission"]
    values = policy_schema(
        {**DEFAULT_OPTIONS, "automatic_submission": True, "automatic_submission_confirmed": True}
    )({})
    assert values["automatic_submission"]
