"""Korean city gas Home Assistant integration."""

from __future__ import annotations

from pathlib import Path

import voluptuous as vol
from homeassistant.components import frontend, panel_custom
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN, PANEL_PATH, VERSION
from .coordinator import AccountCoordinator
from .model import GasError
from .provider import default_region, get_provider
from .websocket import async_register as register_websocket

PLATFORMS = ["sensor", "binary_sensor"]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass, _config):
    data = hass.data.setdefault(DOMAIN, {})
    if not data.get("websocket_registered"):
        register_websocket(hass)
        data["websocket_registered"] = True

    def get(call):
        item = hass.data[DOMAIN].get(call.data["entry_id"])
        if not isinstance(item, AccountCoordinator):
            raise HomeAssistantError("entry_not_loaded")
        return item

    async def refresh(call: ServiceCall):
        if not call.context.user_id:
            raise HomeAssistantError("not_authorized")
        user = await hass.auth.async_get_user(call.context.user_id)
        if not user or not user.is_admin:
            raise HomeAssistantError("not_authorized")
        await get(call).async_request_refresh()

    async def test_notification(call: ServiceCall):
        item = get(call)
        try:
            await item.authorize(call.data["contract_key"], call.context.user_id)
            await item.send_message(
                call.data["contract_key"],
                f"{item.provider.name} 알림 테스트",
                "알림 발송 요청이 정상적으로 처리되었습니다.",
                kind="test",
            )
        except GasError as error:
            raise HomeAssistantError(str(error)) from error

    async def calibrate(call: ServiceCall):
        item = get(call)
        try:
            await item.authorize(call.data["contract_key"], call.context.user_id)
            await item.calibrate(
                call.data["contract_key"], call.data["reading"], physical=call.data["physical"]
            )
        except GasError as error:
            raise HomeAssistantError(str(error)) from error

    async def prepare_submission(call: ServiceCall):
        item = get(call)
        try:
            await item.authorize(call.data["contract_key"], call.context.user_id)
            return item.propose(call.data["contract_key"])
        except GasError as error:
            raise HomeAssistantError(str(error)) from error

    async def submit(call: ServiceCall):
        item = get(call)
        try:
            await item.authorize(call.data["contract_key"], call.context.user_id)
            return await item.submit(call.data["contract_key"], call.data["proposal_id"])
        except GasError as error:
            raise HomeAssistantError(str(error)) from error

    async def check_submission(call: ServiceCall):
        item = get(call)
        try:
            await item.authorize(call.data["contract_key"], call.context.user_id)
            return await item.check_submission(call.data["contract_key"])
        except GasError as error:
            raise HomeAssistantError(str(error)) from error

    schemas = {
        "refresh": (refresh, vol.Schema({vol.Required("entry_id"): str})),
        "test_notification": (
            test_notification,
            vol.Schema({vol.Required("entry_id"): str, vol.Required("contract_key"): str}),
        ),
        "calibrate": (
            calibrate,
            vol.Schema(
                {
                    vol.Required("entry_id"): str,
                    vol.Required("contract_key"): str,
                    vol.Required("reading"): vol.Any(str, int, float),
                    vol.Optional("physical", default=True): bool,
                }
            ),
        ),
    }
    for name, (handler, schema) in schemas.items():
        if not hass.services.has_service(DOMAIN, name):
            hass.services.async_register(DOMAIN, name, handler, schema=schema)
    for name, handler, fields in (
        ("prepare_submission", prepare_submission, {}),
        ("submit", submit, {vol.Required("proposal_id"): str}),
        ("check_submission", check_submission, {}),
    ):
        if not hass.services.has_service(DOMAIN, name):
            hass.services.async_register(
                DOMAIN,
                name,
                handler,
                supports_response=SupportsResponse.ONLY,
                schema=vol.Schema(
                    {vol.Required("entry_id"): str, vol.Required("contract_key"): str, **fields}
                ),
            )
    return True


async def async_setup_entry(hass, entry):
    # Update only our old generated label; preserve user-supplied names.
    contracts = [dict(c) for c in entry.data["contracts"]]
    for contract in contracts:
        if contract["label"].startswith("계약 ••••"):
            contract["label"] = f"계약 {contract['cano']}"
    if contracts != entry.data["contracts"]:
        hass.config_entries.async_update_entry(entry, data={**entry.data, "contracts": contracts})
    coordinator = AccountCoordinator(hass, entry)
    hass.data[DOMAIN][entry.entry_id] = coordinator
    try:
        await coordinator.initialize()
        # Register entities/panel first. First cloud/history reads must not block
        # onboarding completion or a restored local estimate.
    except Exception:
        await coordinator.shutdown()
        hass.data[DOMAIN].pop(entry.entry_id, None)
        raise
    entry.async_on_unload(entry.add_update_listener(update_listener))
    frontend_dir = Path(__file__).parent / "frontend"
    if not hass.data[DOMAIN].get("static_registered"):
        await hass.http.async_register_static_paths(
            [StaticPathConfig(f"/{DOMAIN}_static", str(frontend_dir), True)]
        )
        hass.data[DOMAIN]["static_registered"] = True
    if not hass.data[DOMAIN].get("panel_registered"):
        await panel_custom.async_register_panel(
            hass,
            frontend_url_path=PANEL_PATH,
            webcomponent_name="busan-city-gas-panel",
            sidebar_title="똑똑 자가검침 AI",
            sidebar_icon="mdi:meter-gas",
            module_url=f"/{DOMAIN}_static/panel.js?v={VERSION}",
            require_admin=False,
        )
        hass.data[DOMAIN]["panel_registered"] = True
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    coordinator.initial_refresh_task = hass.async_create_background_task(
        coordinator.async_refresh(), f"{DOMAIN} initial official reads"
    )
    return True


async def async_migrate_entry(hass, entry):
    """Add provider metadata without changing legacy Busan identities or state keys."""
    if entry.version > 2:
        return False
    if entry.version < 2:
        provider = get_provider(entry.data.get("provider_id", "busan"))
        options = {"contracts": {}}
        for key, value in entry.options.get("contracts", {}).items():
            options["contracts"][key] = {
                **value,
                "tariff_region": value.get("tariff_region", default_region(provider)),
                "tariff_profile": value.get("tariff_profile", "residential"),
            }
        hass.config_entries.async_update_entry(
            entry,
            data={**entry.data, "provider_id": provider.id},
            options=options,
            version=2,
        )
    return True


async def update_listener(hass, entry):
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass, entry):
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    coordinator = hass.data[DOMAIN].pop(entry.entry_id)
    await coordinator.shutdown()
    if not any(isinstance(value, AccountCoordinator) for value in hass.data[DOMAIN].values()):
        frontend.async_remove_panel(hass, PANEL_PATH, warn_if_unknown=False)
        hass.data[DOMAIN].pop("panel_registered", None)
    return True
