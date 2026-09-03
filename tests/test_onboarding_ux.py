import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from test_ha import CONTRACT, entry, make_runtime
from test_portal import synthetic_bill

from custom_components.busan_city_gas.config_flow import GasConfigFlow, source_schema
from custom_components.busan_city_gas.const import DOMAIN
from custom_components.busan_city_gas.portal import PortalClient, contracts_from_html


async def test_only_valid_cumulative_volume_sources_in_picker(hass):
    from homeassistant.helpers import entity_registry as er

    registry = SimpleNamespace(async_get=lambda entity_id: None)
    hass.states.async_set(
        "sensor.gas_meter", "0", {"unit_of_measurement": "m³", "state_class": "total_increasing"}
    )
    hass.states.async_set(
        "sensor.temperature", "20", {"unit_of_measurement": "°C", "state_class": "measurement"}
    )
    hass.states.async_set("sensor.estimate", "35", {"unit_of_measurement": "m³"})
    with patch.object(er, "async_get", return_value=registry):
        schema = source_schema(hass, {})
    options = next(iter(schema.schema.values())).config["options"]
    assert [o["value"] for o in options] == ["sensor.gas_meter"]
    assert "m³" in options[0]["label"]
    assert "sensor.gas_meter" in options[0]["label"]
    hass.states.async_remove("sensor.gas_meter")
    assert next(iter(source_schema(hass, {}).schema.values())).config["options"] == []


async def test_login_progress_reuses_task_and_cancel_closes_it(hass):
    gate = asyncio.Event()

    async def slow_login(_):
        await gate.wait()
        return [CONTRACT]

    flow = GasConfigFlow()
    flow.hass = hass
    flow.context = {"source": "user"}
    with patch.object(PortalClient, "contracts", slow_login):
        await flow.async_step_user({"provider_id": "busan"})
        first = await flow.async_step_credentials({"username": "test", "password": "fake"})
        second = await flow.async_step_credentials({"username": "test", "password": "fake"})
        assert first["progress_task"] is second["progress_task"]
        task = flow.login_task
        flow.async_remove()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_setup_finishes_while_cloud_read_is_still_waiting(hass):
    from custom_components.busan_city_gas import async_setup_entry

    runtime = await make_runtime(hass)
    runtime.initialize = AsyncMock()
    gate = asyncio.Event()
    runtime.async_refresh = AsyncMock(side_effect=gate.wait)
    hass.data[DOMAIN] = {"static_registered": True, "panel_registered": True}
    try:
        with (
            patch("custom_components.busan_city_gas.AccountCoordinator", return_value=runtime),
            patch.object(hass.config_entries, "async_forward_entry_setups", AsyncMock()),
        ):
            assert await asyncio.wait_for(async_setup_entry(hass, entry()), timeout=1)
        assert not runtime.initial_refresh_task.done()
        assert runtime.estimates[CONTRACT.key] is not None
    finally:
        await runtime.shutdown()
    assert runtime.initial_refresh_task.done()


async def test_public_tariff_and_private_reads_overlap(hass):
    runtime = await make_runtime(hass)
    private_started, public_started = asyncio.Event(), asyncio.Event()

    async def private():
        private_started.set()
        await public_started.wait()
        return {"updated": "test"}

    async def public():
        public_started.set()
        await private_started.wait()

    runtime._fetch_data = private
    runtime._fetch_tariff = public
    try:
        assert await asyncio.wait_for(runtime._async_update_data(), 1) == {"updated": "test"}
        assert not runtime.refreshing
    finally:
        await runtime.shutdown()


async def test_bill_progress_exposes_latest_before_history():
    client = PortalClient(None, "test", "fake")
    client.bill_page = AsyncMock(return_value=synthetic_bill())
    seen = []
    result = await client.bills(
        CONTRACT, {}, progress=lambda bills, done, total: seen.append((dict(bills), done, total))
    )
    assert seen[0] == (result, 1, 1)


async def test_cached_history_does_not_request_every_month_again():
    client = PortalClient(None, "test", "fake")
    client.bill_page = AsyncMock(
        return_value=synthetic_bill() + "<script>fnGetAskDetail('202607');</script>"
    )
    cached = {"202607": {"preserved": True}}
    result = await client.bills(CONTRACT, cached)
    client.bill_page.assert_awaited_once_with(CONTRACT)
    assert result["202607"] == cached["202607"]


def test_contract_display_not_masked():
    contract = contracts_from_html(
        '<script>f({BPNO:"1234"})</script><input id="list_cano_0" value="11112222">'
    )[0]
    assert contract.label == "계약 11112222"
