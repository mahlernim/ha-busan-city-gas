"""Receipt-only checks never submit or fetch invoice history."""

import asyncio
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError

from custom_components.busan_city_gas import async_setup, websocket
from custom_components.busan_city_gas.const import DOMAIN
from custom_components.busan_city_gas.model import GasError, MeterWindow
from tests.test_ha import CONTRACT, make_runtime


def window(**kwargs):
    return MeterWindow("2026-09-13", "2026-09-18", "27", "meter", eligible=True, **kwargs)


@pytest.mark.parametrize("reading", [None, "35", "36"])
async def test_locked_readback_resolves_only_exact_receipt(hass, reading):
    runtime = await make_runtime(hass)
    key = CONTRACT.key
    fresh = window(submitted=reading)
    runtime.client.meter = AsyncMock(return_value=fresh)
    runtime.client.bills = AsyncMock()
    runtime.client.submit = AsyncMock()
    state = runtime.submissions[key].state
    state[fresh.cycle] = {"status": "uncertain", "proposed": 35, "error": "submission_uncertain"}
    try:
        view = await runtime.check_submission(key)
        assert view["submission_locked"]
        assert view["submission_status"] == ("confirmed" if reading == "35" else "uncertain")
        assert view["submission_observed"] == reading
        assert view["accepted_checked_at"]
        assert not view["submission_checking"]
        runtime.client.submit.assert_not_called()
        runtime.client.bills.assert_not_called()
    finally:
        await runtime.shutdown()


async def test_concurrent_checks_share_one_read_and_cancel_independently(hass):
    runtime = await make_runtime(hass)
    entered, release = asyncio.Event(), asyncio.Event()

    async def meter(_):
        entered.set()
        await release.wait()
        return window()

    runtime.client.meter = AsyncMock(side_effect=meter)
    try:
        first = asyncio.create_task(runtime.check_submission(CONTRACT.key))
        await entered.wait()
        second = asyncio.create_task(runtime.check_submission(CONTRACT.key))
        await asyncio.sleep(0)
        assert runtime.view(CONTRACT.key)["submission_checking"]
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        release.set()
        assert (await second)["submission_status"] == "not_submitted"
        runtime.client.meter.assert_awaited_once()
    finally:
        release.set()
        await runtime.shutdown()


async def test_failed_check_keeps_unknown_intent_and_releases_progress(hass):
    runtime = await make_runtime(hass)
    fresh = window()
    runtime.windows[CONTRACT.key] = fresh
    runtime.submissions[CONTRACT.key].state[fresh.cycle] = {"status": "uncertain", "proposed": 35}
    runtime.client.meter = AsyncMock(side_effect=GasError("cannot_connect"))
    try:
        with pytest.raises(GasError, match="cannot_connect"):
            await runtime.check_submission(CONTRACT.key)
        view = runtime.view(CONTRACT.key)
        assert view["submission_status"] == "uncertain"
        assert view["meter_error"] == "cannot_connect"
        assert not view["submission_checking"]
    finally:
        await runtime.shutdown()


@pytest.mark.parametrize("allowed", [True, False])
@pytest.mark.parametrize("surface", ["service", "panel"])
async def test_readback_entrypoints_require_user_authorization(hass, allowed, surface):
    runtime = await make_runtime(hass)
    runtime.client.meter = AsyncMock(return_value=window())
    runtime.client.submit = AsyncMock()
    hass.data[DOMAIN] = {runtime.entry.entry_id: runtime}
    hass.auth = SimpleNamespace(
        async_get_user=AsyncMock(return_value=SimpleNamespace(is_admin=allowed, is_active=True))
    )
    try:
        if surface == "service":
            await async_setup(hass, {})
            request = hass.services.async_call(
                DOMAIN,
                "check_submission",
                {
                    "entry_id": runtime.entry.entry_id,
                    "contract_key": CONTRACT.key,
                },
                context=Context(user_id="test-user"),
                blocking=True,
                return_response=True,
            )
            if allowed:
                assert (await request)["submission_status"] == "not_submitted"
            else:
                with pytest.raises(HomeAssistantError, match="not_authorized"):
                    await request
        else:
            connection = SimpleNamespace(
                user=SimpleNamespace(id="test-user"), send_result=Mock(), send_error=Mock()
            )
            await inspect.unwrap(websocket.ws_check_submission)(
                hass,
                connection,
                {
                    "id": 1,
                    "entry_id": runtime.entry.entry_id,
                    "key": CONTRACT.key,
                },
            )
            if allowed:
                assert connection.send_result.call_args.args[1]["submission_locked"]
            else:
                assert connection.send_error.call_args.args[1] == "not_authorized"
        assert runtime.client.meter.await_count == int(allowed)
        runtime.client.submit.assert_not_called()
    finally:
        await runtime.shutdown()


async def test_missing_latest_receipt_never_erases_prior_confirmation(hass):
    runtime = await make_runtime(hass)
    fresh = window()
    runtime.submissions[CONTRACT.key].state[fresh.cycle] = {
        "status": "confirmed",
        "accepted": "35",
        "proposed": 35,
    }
    runtime.client.meter = AsyncMock(return_value=fresh)
    try:
        view = await runtime.check_submission(CONTRACT.key)
        assert view["submission_status"] == "confirmed"
        assert view["accepted"] == "35"
        assert view["receipt_in_latest_read"] is False
        assert view["submission_blocked"]
    finally:
        await runtime.shutdown()


async def test_meter_replacement_on_receipt_read_invalidates_estimate(hass):
    runtime = await make_runtime(hass)
    runtime.windows[CONTRACT.key] = window()
    await runtime.calibrate(CONTRACT.key, "35", physical=True)
    replacement = window()
    replacement.meter = "different-meter"
    runtime.client.meter = AsyncMock(return_value=replacement)
    try:
        await runtime.check_submission(CONTRACT.key)
        assert runtime.estimates[CONTRACT.key].gap
        assert runtime.estimates[CONTRACT.key].history[-1]["kind"] == "physical_meter_changed"
        assert runtime.estimates[CONTRACT.key].actual_at is not None
    finally:
        await runtime.shutdown()
