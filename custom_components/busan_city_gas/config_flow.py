"""Progressive onboarding; private credentials never enter panel snapshots."""

from __future__ import annotations

import uuid
from dataclasses import asdict

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.util import dt as dt_util
from homeassistant.util import slugify

from .clients import create_client
from .const import DEFAULT_OPTIONS, DOMAIN
from .gasapp import GasappClient
from .model import Estimate, GasError, decimal
from .portal import (
    AuthenticationError,
    ConnectionError,
    Contract,
    PortalClient,  # noqa: F401 - compatibility for adapter injection
    opaque,
)
from .provider import PROVIDERS, default_profile, default_region, get_provider


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


def policy_schema(defaults: dict, *, deadline: bool = True) -> vol.Schema:
    if not deadline:
        return vol.Schema(
            {
                vol.Optional(
                    "allow_historical_submission",
                    default=defaults.get("allow_historical_submission", False),
                ): bool
            }
            if not defaults.get("source_entity")
            else {}
        )
    fields = {
        vol.Optional(
            "automatic_submission",
            default=bool(
                defaults.get("automatic_submission")
                and defaults.get("automatic_submission_confirmed")
            ),
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
    VERSION = 2

    def __init__(self):
        self.provider = get_provider("busan")
        self.credentials = {}
        self.contracts: list[Contract] = []
        self.selected: list[Contract] = []
        self.settings: dict[str, dict] = {}
        self.position = 0
        self.login_task = None
        self.login_error = None
        self.gasapp_identity = {}
        self.gasapp_terms = []
        self.gasapp_challenge = {}
        self.reauth_entry = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return GasOptionsFlow()

    async def async_step_user(self, user_input=None):
        if user_input is not None:
            self.provider = get_provider(user_input["provider_id"])
            if self.provider.family == "gasapp":
                return await self.async_step_gasapp_identity()
            if self.provider.family == "energytalk":
                return await self.async_step_energytalk_credentials()
            return await self.async_step_credentials()
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required("provider_id", default="busan"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                {"value": key, "label": item.name}
                                for key, item in PROVIDERS.items()
                            ]
                        )
                    )
                }
            ),
        )

    async def async_step_gasapp_identity(self, user_input=None):
        errors = {"base": self.login_error} if self.login_error else {}
        if user_input is not None:
            try:
                GasappClient.validate_identity(user_input)
                self.gasapp_identity = dict(user_input)
                self.credentials = {
                    "gasapp_device_id": self.credentials.get("gasapp_device_id", str(uuid.uuid4()))
                }
                async with aiohttp.ClientSession() as session:
                    self.gasapp_terms = await GasappClient(
                        session, self.credentials, self.provider
                    ).terms(user_input["carrier"])
                self.login_error = None
                return await self.async_step_gasapp_terms()
            except GasError as error:
                errors["base"] = (
                    "invalid_identity" if str(error) == "invalid_identity" else "cannot_connect"
                )
        return self.async_show_form(
            step_id="gasapp_identity",
            data_schema=vol.Schema(
                {
                    vol.Required("name"): str,
                    vol.Required("phone"): str,
                    vol.Required("birthday"): str,
                    vol.Required("gender"): vol.In(["1", "2", "3", "4"]),
                    vol.Required("carrier"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                {"value": "1", "label": "SKT"},
                                {"value": "2", "label": "KT"},
                                {"value": "3", "label": "LG U+"},
                            ]
                        )
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_gasapp_terms(self, user_input=None):
        errors = {}
        if not self.gasapp_identity or not self.gasapp_terms:
            return await self.async_step_gasapp_identity()
        if user_input is not None:
            try:
                async with aiohttp.ClientSession() as session:
                    self.gasapp_challenge = await GasappClient(
                        session, self.credentials, self.provider
                    ).request_sms(
                        self.gasapp_identity, self.gasapp_terms, user_input.get("consent") is True
                    )
                return await self.async_step_gasapp_sms()
            except GasError as error:
                errors["base"] = (
                    "consent_required" if str(error) == "consent_required" else "cannot_connect"
                )
        return self.async_show_form(
            step_id="gasapp_terms",
            data_schema=vol.Schema(
                {
                    vol.Required("consent", default=False): bool,
                }
            ),
            description_placeholders={
                "terms": "\n\n".join(t["category"] + "\n" + t["text"] for t in self.gasapp_terms)
            },
            errors=errors,
        )

    async def async_step_gasapp_sms(self, user_input=None):
        errors = {}
        if not self.gasapp_challenge:
            return await self.async_step_gasapp_identity()
        if user_input is not None:
            try:
                async with aiohttp.ClientSession() as session:
                    self.credentials = await GasappClient(
                        session, self.credentials, self.provider
                    ).confirm_sms(self.gasapp_identity, self.gasapp_challenge, user_input["otp"])
                self.gasapp_identity, self.gasapp_terms, self.gasapp_challenge = {}, [], {}
                self.login_task, self.login_error = None, None
                return await self.async_step_login()
            except GasError as error:
                errors["base"] = "invalid_otp" if str(error) == "invalid_otp" else "cannot_connect"
        return self.async_show_form(
            step_id="gasapp_sms", data_schema=vol.Schema({vol.Required("otp"): str}), errors=errors
        )

    async def async_step_energytalk_credentials(self, user_input=None):
        errors = {"base": self.login_error} if self.login_error else {}
        if user_input is not None:
            token = user_input.get("energytalk_token", "").strip()
            if token and not any(char.isspace() for char in token):
                self.credentials = {"energytalk_token": token}
                self.login_error, self.login_task = None, None
                return await self.async_step_login()
            errors["base"] = "invalid_auth"
        return self.async_show_form(
            step_id="energytalk_credentials",
            data_schema=vol.Schema(
                {
                    vol.Required("energytalk_token"): selector.TextSelector(
                        selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
                    )
                }
            ),
            description_placeholders={"provider": self.provider.name},
            errors=errors,
        )

    async def async_step_credentials(self, user_input=None):
        errors = {"base": self.login_error} if self.login_error else {}
        if user_input is not None:
            if self.login_task and not self.login_task.done():
                return await self.async_step_login()
            self.credentials = dict(user_input)
            self.login_error = None
            self.login_task = None
            return await self.async_step_login()
        return self.async_show_form(
            step_id="credentials",
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
                client = create_client(session, self.credentials, self.provider)
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
            if self.provider.family == "gasapp":
                return await self.async_step_gasapp_identity()
            if self.provider.family == "energytalk":
                return await self.async_step_energytalk_credentials()
            self.credentials = {}
            return await self.async_step_credentials()
        if not self.contracts:
            return self.async_abort(reason="no_contracts")
        if self.reauth_entry is not None:
            existing = {c["key"] for c in self.reauth_entry.data["contracts"]}
            if not existing.issubset({c.key for c in self.contracts}):
                return self.async_abort(reason="wrong_account")
            return self.async_update_reload_and_abort(
                self.reauth_entry,
                data_updates={
                    **self.credentials,
                    "contracts": [
                        asdict(next(c for c in self.contracts if c.key == old["key"]))
                        for old in self.reauth_entry.data["contracts"]
                    ],
                },
            )
        await self.async_set_unique_id(
            opaque(
                f"{self.provider.code}:"
                + (
                    self.credentials["username"]
                    if self.provider.family in ("samchully", "daesung", "haeyang")
                    else self.contracts[0].bpno or self.contracts[0].key
                )
            )
        )
        self._abort_if_unique_id_configured()
        if len(self.contracts) == 1:
            self.selected = self.contracts
            return await self.async_step_tariff()
        return await self.async_step_contracts()

    @callback
    def async_remove(self):
        self.gasapp_identity, self.gasapp_terms, self.gasapp_challenge = {}, [], {}
        self.credentials = {}
        if self.login_task and not self.login_task.done():
            self.login_task.cancel()
        super().async_remove()

    async def async_step_contracts(self, user_input=None):
        if user_input and user_input.get("contracts"):
            self.selected = [item for item in self.contracts if item.key in user_input["contracts"]]
            if self.selected:
                return await self.async_step_tariff()
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

    async def async_step_tariff(self, user_input=None):
        if not self.provider.supports_tariff:
            return await self.async_step_source()
        regions = self.provider.regions
        profiles = self.provider.profiles
        if user_input is not None:
            region = user_input["tariff_region"]
            profile = user_input.get("tariff_profile", default_profile(self.provider))
            if region not in {value for value, _label in regions} or profile not in {
                value for value, _label, _match in profiles
            }:
                return self.async_show_form(step_id="tariff", errors={"base": "invalid_tariff"})
            self.current.update(tariff_region=region, tariff_profile=profile)
            return await self.async_step_source()
        return self.async_show_form(
            step_id="tariff",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "tariff_region", default=default_region(self.provider)
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[{"value": value, "label": label} for value, label in regions]
                        )
                    ),
                    vol.Required(
                        "tariff_profile", default=default_profile(self.provider)
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                {"value": value, "label": label}
                                for value, label, _match in profiles
                            ]
                        )
                    ),
                }
            ),
            description_placeholders={"contract": self.selected[self.position].label},
        )

    async def async_step_source(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                source = user_input.get("source_entity", "")
                validate_source(self.hass, source)
                self.current["source_entity"] = source
                return await self.async_step_anchor()
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
                if self.current["source_entity"] and source is None:
                    raise GasError("invalid_source")
                if source:
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
                        user_input["reading"],
                        source.state if source else None,
                        dt_util.now(),
                        physical=True,
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
            if not self.provider.supports_deadline:
                self.current["automatic_submission"] = False
            self.current["weekly_day"] = int(self.current["weekly_day"])
            return await self.async_step_policy()
        return self.async_show_form(
            step_id="notifications", data_schema=notification_schema(self.hass, self.current)
        )

    async def async_step_policy(self, user_input=None):
        if user_input is not None:
            self.current.update(user_input)
            if not self.provider.supports_deadline:
                self.current["automatic_submission"] = False
            self.current["automatic_submission_confirmed"] = True
            if self.current["source_entity"]:
                self.current["allow_historical_submission"] = False
            self.position += 1
            if self.position < len(self.selected):
                return await self.async_step_tariff()
            return await self.async_step_summary()
        return self.async_show_form(
            step_id="policy",
            data_schema=policy_schema(self.current, deadline=self.provider.supports_deadline),
        )

    async def async_step_summary(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(
                title=self.provider.name,
                data={
                    **self.credentials,
                    "provider_id": self.provider.id,
                    "contracts": [asdict(c) for c in self.selected],
                },
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
                f"{contract.label}: 센서 {o['source_entity'] or '없음'}, 기준 {o.get('initial_estimate', {}).get('value') or '미설정'} m³, 수신 {recipients}, 보정 {o['weekly_time']}, 제출 알림 {o['reminder_time']}, 마감 {o['deadline_time']}, 자동 제출 {'켜짐' if o['automatic_submission'] else '꺼짐'}, 과거값 허용 {'허용' if o['allow_historical_submission'] else '허용 안 함'}"
            )
        return self.async_show_form(
            step_id="summary",
            data_schema=vol.Schema({}),
            description_placeholders={"summary": "\n\n".join(summaries)},
            last_step=True,
        )

    async def async_step_reauth(self, entry_data):
        self.reauth_entry = self._get_reauth_entry()
        self.provider = get_provider(entry_data.get("provider_id", "busan"))
        if self.provider.family == "energytalk":
            return await self.async_step_energytalk_credentials()
        if self.provider.family == "gasapp":
            self.credentials = {
                "gasapp_device_id": entry_data.get("gasapp_device_id", str(uuid.uuid4()))
            }
            return await self.async_step_gasapp_identity()
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        errors = {}
        if user_input:
            entry = self._get_reauth_entry()
            provider = get_provider(entry.data.get("provider_id", "busan"))
            try:
                async with aiohttp.ClientSession() as session:
                    contracts = await create_client(
                        session, {**entry.data, "password": user_input["password"]}, provider
                    ).contracts()
                if not contracts or (
                    not {c["key"] for c in entry.data["contracts"]}.issubset(
                        {c.key for c in contracts}
                    )
                ):
                    return self.async_abort(reason="wrong_account")
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={
                        "password": user_input["password"],
                        "contracts": [
                            asdict(next(c for c in contracts if c.key == old["key"]))
                            for old in entry.data["contracts"]
                        ],
                    },
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
        provider = get_provider(self.config_entry.data.get("provider_id", "busan"))
        menus = (["tariff"] if provider.supports_tariff else []) + [
            "source",
            "notifications",
            "policy",
        ]
        return self.async_show_menu(step_id="menu", menu_options=menus)

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

    async def async_step_tariff(self, user_input=None):
        provider = get_provider(self.config_entry.data.get("provider_id", "busan"))
        regions = provider.regions
        profiles = provider.profiles
        if user_input is not None:
            return self.finish(
                {
                    "tariff_region": user_input["tariff_region"],
                    "tariff_profile": user_input.get("tariff_profile", default_profile(provider)),
                }
            )
        return self.async_show_form(
            step_id="tariff",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "tariff_region",
                        default=self.current.get("tariff_region", default_region(provider)),
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[{"value": value, "label": label} for value, label in regions]
                        )
                    ),
                    vol.Required(
                        "tariff_profile",
                        default=self.current.get("tariff_profile", default_profile(provider)),
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                {"value": value, "label": label}
                                for value, label, _match in profiles
                            ]
                        )
                    ),
                }
            ),
        )

    async def async_step_notifications(self, user_input=None):
        if user_input is not None:
            return self.finish(user_input)
        return self.async_show_form(
            step_id="notifications", data_schema=notification_schema(self.hass, self.current)
        )

    async def async_step_policy(self, user_input=None):
        provider = get_provider(self.config_entry.data.get("provider_id", "busan"))
        if user_input is not None:
            if not provider.supports_deadline:
                user_input = {**user_input, "automatic_submission": False}
            return self.finish({**user_input, "automatic_submission_confirmed": True})
        return self.async_show_form(
            step_id="policy",
            data_schema=policy_schema(self.current, deadline=provider.supports_deadline),
        )
