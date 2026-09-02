"""Progressive onboarding; private credentials never enter panel snapshots."""

from __future__ import annotations

from dataclasses import asdict

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.util import dt as dt_util
from homeassistant.util import slugify

from .const import DEFAULT_OPTIONS, DOMAIN
from .model import Estimate, GasError, decimal
from .portal import AuthenticationError, ConnectionError, Contract, PortalClient, opaque


def phones(hass) -> dict[str, dict]:
    result = {}
    for entry in hass.config_entries.async_entries("mobile_app"):
        service = f"mobile_app_{slugify(entry.data.get('device_name', entry.title))}"
        if entry.data.get("user_id") and hass.services.has_service("notify", service):
            result[entry.entry_id] = {
                "label": entry.title,
                "user_id": entry.data["user_id"],
                "device_id": entry.data.get("device_id"),
                "service": service,
            }
    return result


def source_schema(hass, defaults: dict) -> vol.Schema:
    candidates = []
    for state in hass.states.async_all("sensor"):
        try:
            validate_source(hass, state.entity_id)
        except GasError:
            continue
        candidates.append(
            {
                "value": state.entity_id,
                "label": f"{state.name} · {state.attributes['unit_of_measurement']} · {state.entity_id}",
            }
        )
    field = (
        vol.Optional("source_entity", description={"suggested_value": defaults["source_entity"]})
        if defaults.get("source_entity")
        else vol.Optional("source_entity")
    )
    return vol.Schema(
        {
            field: selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=candidates,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            )
        }
    )


def validate_source(hass, entity_id: str) -> None:
    if not entity_id:
        return
    state = hass.states.get(entity_id)
    if state is None or state.attributes.get("unit_of_measurement") not in ("m³", "m3", "㎥"):
        raise GasError("invalid_source")
    if state.attributes.get("state_class") not in ("total", "total_increasing"):
        raise GasError("invalid_source")
    # Utility meters reset by calendar, unlike a raw restart-resetting counter.
    from homeassistant.helpers import entity_registry as er

    registry_entry = er.async_get(hass).async_get(entity_id)
    if registry_entry and registry_entry.platform == "utility_meter":
        raise GasError("invalid_source")


def notification_schema(hass, defaults: dict) -> vol.Schema:
    options = [{"value": key, "label": item["label"]} for key, item in phones(hass).items()]
    return vol.Schema(
        {
            vol.Optional(
                "recipients", default=defaults.get("recipients", [])
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(options=options, multiple=True)
            ),
            vol.Optional("weekly_enabled", default=defaults.get("weekly_enabled", False)): bool,
            vol.Optional(
                "weekly_day", default=str(defaults.get("weekly_day", 5))
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        {"value": str(i), "label": name}
                        for i, name in enumerate(["월", "화", "수", "목", "금", "토", "일"])
                    ]
                )
            ),
            vol.Optional(
                "weekly_time", default=defaults.get("weekly_time", "10:00:00")
            ): selector.TimeSelector(),
            vol.Optional("reminder_enabled", default=defaults.get("reminder_enabled", False)): bool,
            vol.Optional(
                "reminder_time", default=defaults.get("reminder_time", "22:00:00")
            ): selector.TimeSelector(),
        }
    )


def policy_schema(defaults: dict) -> vol.Schema:
    fields = {
        vol.Optional(
            "automatic_submission", default=defaults.get("automatic_submission", False)
        ): bool,
        vol.Optional(
            "deadline_time", default=defaults.get("deadline_time", "22:00:00")
        ): selector.TimeSelector(),
    }
    if not defaults.get("source_entity"):
        fields[
            vol.Optional(
                "allow_historical_submission",
                default=defaults.get("allow_historical_submission", False),
            )
        ] = bool
    return vol.Schema(fields)


class GasConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self):
        self.credentials = {}
        self.contracts: list[Contract] = []
        self.selected: list[Contract] = []
        self.settings: dict[str, dict] = {}
        self.position = 0
        self.login_task = None
        self.login_error = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return GasOptionsFlow()

    async def async_step_user(self, user_input=None):
        errors = {"base": self.login_error} if self.login_error else {}
        if user_input is not None:
            if self.login_task and not self.login_task.done():
                return await self.async_step_login()
            self.credentials = dict(user_input)
            self.login_error = None
            self.login_task = None
            return await self.async_step_login()
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required("username"): str,
                    vol.Required("password"): selector.TextSelector(
                        selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
                    ),
                }
            ),
            errors=errors,
        )

    async def _login(self):
        try:
            async with aiohttp.ClientSession() as session:
                client = PortalClient(
                    session, self.credentials["username"], self.credentials["password"]
                )
                self.contracts = await client.contracts()
        except AuthenticationError:
            self.login_error = "invalid_auth"
        except ConnectionError:
            self.login_error = "cannot_connect"
        except GasError:
            self.login_error = "schema_changed"

    async def async_step_login(self, user_input=None):
        if self.login_task is None:
            self.login_task = self.hass.async_create_task(self._login())
        if not self.login_task.done():
            return self.async_show_progress(
                step_id="login",
                progress_action="login",
                progress_task=self.login_task,
            )
        self.login_task.result()
        return self.async_show_progress_done(next_step_id="login_result")

    async def async_step_login_result(self, user_input=None):
        if self.login_error:
            self.credentials = {}
            return await self.async_step_user()
        if not self.contracts:
            return self.async_abort(reason="no_contracts")
        await self.async_set_unique_id(opaque("C000:" + self.contracts[0].bpno))
        self._abort_if_unique_id_configured()
        if len(self.contracts) == 1:
            self.selected = self.contracts
            return await self.async_step_source()
        return await self.async_step_contracts()

    @callback
    def async_remove(self):
        if self.login_task and not self.login_task.done():
            self.login_task.cancel()
        super().async_remove()

    async def async_step_contracts(self, user_input=None):
        if user_input and user_input.get("contracts"):
            self.selected = [item for item in self.contracts if item.key in user_input["contracts"]]
            if self.selected:
                return await self.async_step_source()
        return self.async_show_form(
            step_id="contracts",
            data_schema=vol.Schema(
                {
                    vol.Required("contracts"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[{"value": c.key, "label": c.label} for c in self.contracts],
                            multiple=True,
                        )
                    )
                }
            ),
        )

    @property
    def current(self):
        return self.settings.setdefault(self.selected[self.position].key, dict(DEFAULT_OPTIONS))

    async def async_step_source(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                source = user_input.get("source_entity", "")
                validate_source(self.hass, source)
                self.current["source_entity"] = source
                if source:
                    return await self.async_step_anchor()
                return await self.async_step_notifications()
            except GasError:
                errors["base"] = "invalid_source"
        return self.async_show_form(
            step_id="source",
            data_schema=source_schema(self.hass, self.current),
            errors=errors,
            description_placeholders={"contract": self.selected[self.position].label},
        )

    async def async_step_anchor(self, user_input=None):
        errors = {}
        legacy = self.hass.states.get("sensor.gas_meter_estimated_reading")
        can_import = (
            self.current["source_entity"] == "sensor.gas_meter"
            and legacy is not None
            and legacy.attributes.get("last_actual_at") is not None
        )
        if user_input is not None:
            try:
                source = self.hass.states.get(self.current["source_entity"])
                if source is None:
                    raise GasError("invalid_source")
                decimal(source.state)
                estimate = Estimate()
                if user_input.get("import_existing") and can_import:
                    estimate = Estimate(
                        value=str(decimal(legacy.state)),
                        source_last=str(decimal(legacy.attributes["source_last_value"])),
                        source_at=legacy.last_updated.isoformat(),
                        actual=str(decimal(legacy.attributes["last_actual_reading"])),
                        actual_at=legacy.attributes["last_actual_at"],
                        gap=False,
                    )
                    estimate.observe(source.state, dt_util.now())
                elif "reading" in user_input:
                    estimate.calibrate(
                        user_input["reading"], source.state, dt_util.now(), physical=True
                    )
                self.current["initial_estimate"] = estimate.dump()
                return await self.async_step_notifications()
            except (GasError, KeyError, ValueError):
                errors["base"] = "invalid_reading"
        fields = {
            vol.Optional("reading"): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=99999, step=0.1, mode="box")
            )
        }
        if can_import:
            fields[vol.Optional("import_existing", default=False)] = bool
        return self.async_show_form(
            step_id="anchor",
            data_schema=vol.Schema(fields),
            errors=errors,
            description_placeholders={
                "existing": f"{legacy.state} m³ ({legacy.attributes.get('last_actual_at')})"
                if can_import
                else "없음"
            },
        )

    async def async_step_notifications(self, user_input=None):
        if user_input is not None:
            self.current.update(user_input)
            self.current["weekly_day"] = int(self.current["weekly_day"])
            return await self.async_step_policy()
        return self.async_show_form(
            step_id="notifications", data_schema=notification_schema(self.hass, self.current)
        )

    async def async_step_policy(self, user_input=None):
        if user_input is not None:
            self.current.update(user_input)
            if self.current["source_entity"]:
                self.current["allow_historical_submission"] = False
            self.position += 1
            if self.position < len(self.selected):
                return await self.async_step_source()
            return await self.async_step_summary()
        return self.async_show_form(step_id="policy", data_schema=policy_schema(self.current))

    async def async_step_summary(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(
                title="부산도시가스",
                data={**self.credentials, "contracts": [asdict(c) for c in self.selected]},
                options={"contracts": self.settings},
            )
        summaries = []
        for contract in self.selected:
            o = self.settings[contract.key]
            recipients = (
                ", ".join(
                    phones(self.hass)[key]["label"]
                    for key in o["recipients"]
                    if key in phones(self.hass)
                )
                or "없음"
            )
            summaries.append(
                f"{contract.label}: 센서 {o['source_entity'] or '없음'}, 기준 {o.get('initial_estimate', {}).get('value') or '미설정'} m³, 수신 {recipients}, 보정 {o['weekly_time']}, 제출 알림 {o['reminder_time']}, 마감 {o['deadline_time']}, 자동 제출 {'요청됨(검증 잠금)' if o['automatic_submission'] else '꺼짐'}, 과거값 허용 {o['allow_historical_submission']}"
            )
        return self.async_show_form(
            step_id="summary",
            data_schema=vol.Schema({}),
            description_placeholders={"summary": "\n\n".join(summaries)},
            last_step=True,
        )

    async def async_step_reauth(self, entry_data):
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        errors = {}
        if user_input:
            entry = self._get_reauth_entry()
            try:
                async with aiohttp.ClientSession() as session:
                    contracts = await PortalClient(
                        session, entry.data["username"], user_input["password"]
                    ).contracts()
                if not contracts or opaque("C000:" + contracts[0].bpno) != entry.unique_id:
                    return self.async_abort(reason="wrong_account")
                return self.async_update_reload_and_abort(
                    entry, data_updates={"password": user_input["password"]}
                )
            except AuthenticationError:
                errors["base"] = "invalid_auth"
            except GasError:
                errors["base"] = "cannot_connect"
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required("password"): selector.TextSelector(
                        selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
                    )
                }
            ),
            errors=errors,
        )


class GasOptionsFlow(config_entries.OptionsFlow):
    def __init__(self):
        self.key = None

    async def async_step_init(self, user_input=None):
        contracts = self.config_entry.data["contracts"]
        if len(contracts) == 1:
            self.key = contracts[0]["key"]
            return await self.async_step_menu()
        if user_input:
            self.key = user_input["contract"]
            return await self.async_step_menu()
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required("contract"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[{"value": c["key"], "label": c["label"]} for c in contracts]
                        )
                    )
                }
            ),
        )

    @property
    def current(self):
        return {
            **DEFAULT_OPTIONS,
            **self.config_entry.options.get("contracts", {}).get(self.key, {}),
        }

    async def async_step_menu(self, user_input=None):
        return self.async_show_menu(
            step_id="menu", menu_options=["source", "notifications", "policy"]
        )

    def finish(self, values):
        current = {**self.current, **values}
        if current["source_entity"]:
            current["allow_historical_submission"] = False
        current["weekly_day"] = int(current["weekly_day"])
        contracts = dict(self.config_entry.options.get("contracts", {}))
        contracts[self.key] = current
        return self.async_create_entry(title="", data={"contracts": contracts})

    async def async_step_source(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                validate_source(self.hass, user_input.get("source_entity", ""))
                return self.finish({"source_entity": user_input.get("source_entity", "")})
            except GasError:
                errors["base"] = "invalid_source"
        return self.async_show_form(
            step_id="source", data_schema=source_schema(self.hass, self.current), errors=errors
        )

    async def async_step_notifications(self, user_input=None):
        if user_input is not None:
            return self.finish(user_input)
        return self.async_show_form(
            step_id="notifications", data_schema=notification_schema(self.hass, self.current)
        )

    async def async_step_policy(self, user_input=None):
        if user_input is not None:
            return self.finish(user_input)
        return self.async_show_form(step_id="policy", data_schema=policy_schema(self.current))
