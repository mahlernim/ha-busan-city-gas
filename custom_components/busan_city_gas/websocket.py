"""Panel API. Reads and writes have the same authorization as integration actions."""

from __future__ import annotations

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import callback

from .const import DOMAIN, EVENT_UPDATED
from .model import GasError


def runtime(hass, entry_id):
    try:
        return hass.data[DOMAIN][entry_id]
    except KeyError:
        raise GasError("entry_not_loaded") from None


def fail(connection, msg, error):
    code = str(error) if isinstance(error, GasError) else "operation_failed"
    connection.send_error(msg["id"], code, code)


async def allowed(runtime_, connection, key):
    await runtime_.authorize(key, connection.user.id)


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/list"})
@websocket_api.async_response
async def ws_list(hass, connection, msg):
    result = []
    for item in hass.data.get(DOMAIN, {}).values():
        if not hasattr(item, "contracts"):
            continue
        for key in item.contracts:
            try:
                await allowed(item, connection, key)
                result.append(item.view(key))
            except GasError:
                continue
    connection.send_result(msg["id"], result)


BASE = {vol.Required("entry_id"): str, vol.Required("key"): str}


@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/refresh", vol.Required("entry_id"): str}
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_refresh(hass, connection, msg):
    try:
        await runtime(hass, msg["entry_id"]).async_request_refresh()
        connection.send_result(msg["id"])
    except Exception as error:
        fail(connection, msg, error)


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/calibrate",
        **BASE,
        vol.Required("reading"): vol.Any(str, int, float),
        vol.Optional("physical", default=True): bool,
        vol.Optional("expected"): vol.Any(str, int, float),
        vol.Optional("accept_large", default=False): bool,
        vol.Optional("replace_meter", default=False): bool,
    }
)
@websocket_api.async_response
async def ws_calibrate(hass, connection, msg):
    try:
        item = runtime(hass, msg["entry_id"])
        await allowed(item, connection, msg["key"])
        await item.calibrate(
            msg["key"],
            msg["reading"],
            physical=msg["physical"],
            expected=msg.get("expected"),
            accept_large=msg["accept_large"],
            replace_meter=msg["replace_meter"],
        )
        connection.send_result(msg["id"], item.view(msg["key"]))
    except Exception as error:
        fail(connection, msg, error)


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/proposal", **BASE})
@websocket_api.async_response
async def ws_proposal(hass, connection, msg):
    try:
        item = runtime(hass, msg["entry_id"])
        await allowed(item, connection, msg["key"])
        connection.send_result(msg["id"], item.propose(msg["key"]))
    except Exception as error:
        fail(connection, msg, error)


@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/submit", **BASE, vol.Required("proposal_id"): str}
)
@websocket_api.async_response
async def ws_submit(hass, connection, msg):
    try:
        item = runtime(hass, msg["entry_id"])
        await allowed(item, connection, msg["key"])
        connection.send_result(msg["id"], await item.submit(msg["key"], msg["proposal_id"]))
    except Exception as error:
        fail(connection, msg, error)


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/check_submission", **BASE})
@websocket_api.async_response
async def ws_check_submission(hass, connection, msg):
    try:
        item = runtime(hass, msg["entry_id"])
        await allowed(item, connection, msg["key"])
        connection.send_result(msg["id"], await item.check_submission(msg["key"]))
    except Exception as error:
        fail(connection, msg, error)


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/prepare_service",
        **BASE,
        vol.Required("action"): vol.In(["register", "channel"]),
        vol.Required("consent"): bool,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_prepare_service(hass, connection, msg):
    try:
        item = runtime(hass, msg["entry_id"])
        await allowed(item, connection, msg["key"])
        connection.send_result(
            msg["id"], await item.prepare_service(msg["key"], msg["action"], msg["consent"])
        )
    except Exception as error:
        fail(connection, msg, error)


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/test_notification", **BASE})
@websocket_api.async_response
async def ws_test_notification(hass, connection, msg):
    try:
        item = runtime(hass, msg["entry_id"])
        await allowed(item, connection, msg["key"])
        connection.send_result(
            msg["id"],
            await item.send_message(
                msg["key"],
                f"{item.provider.name} 알림 테스트",
                "알림 발송 요청이 정상적으로 처리되었습니다.",
                kind="test",
            ),
        )
    except Exception as error:
        fail(connection, msg, error)


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/subscribe"})
@callback
def ws_subscribe(hass, connection, msg):
    @callback
    def updated(_event):
        connection.send_event(msg["id"], {"updated": True})

    connection.subscriptions[msg["id"]] = hass.bus.async_listen(EVENT_UPDATED, updated)
    connection.send_result(msg["id"])


def async_register(hass):
    for command in (
        ws_list,
        ws_refresh,
        ws_calibrate,
        ws_proposal,
        ws_submit,
        ws_check_submission,
        ws_prepare_service,
        ws_test_notification,
        ws_subscribe,
    ):
        websocket_api.async_register_command(hass, command)
