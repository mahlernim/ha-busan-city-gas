"""Account runtime: independent local estimates, shared reads, timers and notices."""

from __future__ import annotations

import asyncio
import logging
import secrets
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timedelta

import aiohttp
from homeassistant.components import persistent_notification
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .clients import create_client
from .config_flow import phones
from .const import (
    DEFAULT_OPTIONS,
    DOMAIN,
    EVENT_UPDATED,
    PANEL_PATH,
    SUBMISSION_ENABLED,
)
from .model import Bill, Estimate, GasError, MeterWindow, Tariff, decimal, forecast
from .portal import AuthenticationError, Contract, PortalClient
from .provider import default_region, get_provider
from .sensorless import SensorlessModel
from .submission import SubmissionManager

LOGGER = logging.getLogger(__name__)


class AccountCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, entry):
        super().__init__(
            hass, LOGGER, name=DOMAIN, config_entry=entry, update_interval=timedelta(days=1)
        )
        self.entry = entry
        self.provider = get_provider(entry.data.get("provider_id", "busan"))
        self.store = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}", private=True)
        self.session = aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar())
        self.client = create_client(self.session, entry.data, self.provider)
        self.contracts = {c["key"]: Contract(**c) for c in entry.data["contracts"]}
        self.saved: dict = {}
        self.estimates: dict[str, Estimate] = {}
        self.windows: dict[str, MeterWindow] = {}
        self.submissions: dict[str, SubmissionManager] = {}
        self.prompts: dict[str, dict] = {}
        self.removers = []
        self.action_lock = asyncio.Lock()
        self.tick_lock = asyncio.Lock()
        self.save_lock = asyncio.Lock()
        self.ready_at = dt_util.now()
        self.initial_refresh_task = None
        self.refreshing = False
        self.progress = {}
        self.receipt_tasks = {}
        self.models = {}
        self.startup_deadlines = {}
        self._closing = False

    def options(self, key):
        options = {**DEFAULT_OPTIONS, **self.entry.options.get("contracts", {}).get(key, {})}
        # Old releases promised that this option could not send anything.
        # Only saving the new policy form grants automatic-write consent.
        options["automatic_submission"] = bool(
            options["automatic_submission"] and options["automatic_submission_confirmed"]
        )
        return options

    async def initialize(self):
        self.saved = await self.store.async_load() or {"contracts": {}}
        for key, contract in self.contracts.items():
            data = self.saved["contracts"].setdefault(key, {})
            initial = data.get("estimate") or self.options(key).get("initial_estimate") or {}
            self.estimates[key] = Estimate(**initial)
            source = self.options(key)["source_entity"]
            if data.get("estimate") and source and self.estimates[key].source_at:
                deadline = datetime.fromisoformat(self.estimates[key].source_at) + timedelta(
                    seconds=120
                )
                if (
                    dt_util.now() > deadline
                    or datetime.fromisoformat(self.estimates[key].source_at) > dt_util.now()
                ):
                    self.estimates[key].invalidate(dt_util.now())
                elif not self.estimates[key].gap and self.estimates[key].source_last is not None:
                    self.startup_deadlines[key] = deadline
            if data.get("estimate") and data.get("source") != source:
                self.estimates[key].source_last = None
                self.estimates[key].source_at = None
                self.estimates[key].gap = True
                self.startup_deadlines.pop(key, None)
            data["source"] = source
            if data.get("window"):
                self.windows[key] = MeterWindow(**data["window"])
            self.models[key] = SensorlessModel(data.get("sensorless"))
            self.sync_model(key)
            state = data.setdefault("submissions", {})

            async def query(k=key, c=contract):
                window = await self.client.meter(c)
                previous = self.windows.get(k)
                known_meter = self.saved["contracts"][k].get("last_meter") or (
                    previous.meter if previous else None
                )
                if known_meter and window.meter and known_meter != window.meter:
                    self.estimates[k].invalidate(dt_util.now())
                    self.estimates[k].record("physical_meter_changed", dt_util.now())
                if window.meter:
                    self.saved["contracts"][k]["last_meter"] = window.meter
                self.windows[k] = window
                self.saved["contracts"][k]["window"] = asdict(window)
                self.sync_model(k)
                return window

            async def write(window, value, c=contract):
                await self.client.submit(c, window, value, now=dt_util.now())

            self.submissions[key] = SubmissionManager(
                state,
                self.persist,
                query,
                write,
                enabled=SUBMISSION_ENABLED and self.provider.supports_submission,
            )
            if source:
                self.observe(key)

                @callback
                def on_source(event, k=key):
                    self.hass.async_create_task(self.source_changed(k))

                self.removers.append(async_track_state_change_event(self.hass, source, on_source))
        self.removers.extend(
            [
                self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, self._stop_observing),
                async_track_time_interval(self.hass, self.tick, timedelta(minutes=1)),
                self.hass.bus.async_listen(
                    "mobile_app_notification_action", self.notification_action
                ),
                self.hass.bus.async_listen(
                    "mobile_app_notification_received", self.notification_received
                ),
            ]
        )
        await self.persist()

    @callback
    def changed(self):
        self.async_update_listeners()
        self.hass.bus.async_fire(EVENT_UPDATED, {"entry_id": self.entry.entry_id})

    @callback
    def _stop_observing(self, event):
        # ESPHome disconnects at CLOSE, when HA is already NOT_RUNNING and
        # is_stopping is false. Remember STOP through the remaining phases.
        self._closing = True

    def observe(self, key):
        # Shutdown disconnects are expected; keep the last valid timestamp so
        # restart grace still expires from that checkpoint, never from shutdown.
        if self._closing or self.hass.is_stopping:
            return
        source = self.options(key)["source_entity"]
        if source:
            state = self.hass.states.get(source)
            now = dt_util.now()
            raw = state.state if state and not state.attributes.get("restored") else None
            try:
                decimal(raw)
                valid = True
            except GasError:
                valid = False
            estimate = self.estimates[key]
            # A brief transport disconnect can recover the cumulative cursor.
            # Reuse the bounded restart grace, anchored to the last valid sample
            # so repeated unavailable events and reloads cannot extend it.
            if (
                not valid
                and key not in self.startup_deadlines
                and not estimate.gap
                and estimate.source_last is not None
                and estimate.source_at
            ):
                source_at = datetime.fromisoformat(estimate.source_at)
                if source_at <= now:
                    self.startup_deadlines[key] = source_at + timedelta(seconds=120)
            if deadline := self.startup_deadlines.get(key):
                if now < deadline and not valid:
                    source_at = datetime.fromisoformat(self.estimates[key].source_at)
                    for affected in {source_at.date(), now.date()}:
                        day = self.estimates[key].days.setdefault(
                            affected.isoformat(), {"volume": "0"}
                        )
                        day.update(complete=False, invalid=True)
                    return
                self.startup_deadlines.pop(key, None)
                if now >= deadline:
                    self.estimates[key].invalidate(now)
            self.estimates[key].observe(raw, now)

    def sync_model(self, key):
        item = self.saved["contracts"][key]
        self.models[key].sync(
            [Bill.load(b) for b in item.get("bills", {}).values()],
            self.windows.get(key),
            self.estimates[key],
            dt_util.now(),
        )

        model, window = self.models[key], self.windows.get(key)
        if window and window.meter and not model.state.get("checkpoint"):
            readings = getattr(self.client, "readings", {}).get(key, [])
            matching = [
                r
                for r in readings
                if r.get("meter") == window.meter and r["date"] <= dt_util.now().date().isoformat()
            ]
            if matching:
                latest = max(matching, key=lambda r: r["date"])
                at = datetime.fromisoformat(latest["date"]).replace(tzinfo=dt_util.now().tzinfo)
                model.state["checkpoint"] = {"at": at.isoformat(), "value": latest["value"]}
                model.state["anchor_kind"] = "official"

    async def source_changed(self, key):
        async with self.action_lock:
            if self._closing:
                return
            self.observe(key)
            self.changed()
            # Cursor and estimate are stored together. Debounce source-only writes;
            # calibration and write-ahead submission use awaited persistence.
            self.store.async_delay_save(self.dump, 5)

    def dump(self):
        for key, estimate in self.estimates.items():
            self.saved["contracts"][key]["estimate"] = estimate.dump()
            self.saved["contracts"][key]["sensorless"] = self.models[key].state
        return self.saved

    async def persist(self):
        async with self.save_lock:
            await self.store.async_save(self.dump())

    async def _async_update_data(self):
        self.refreshing = True
        self.changed()
        try:
            # Public tariff retrieval is independent of the serialized private
            # session. Keep concurrency bounded to these two read-only lanes.
            results = await asyncio.gather(
                self._fetch_data(), self._fetch_tariff(), return_exceptions=True
            )
            for result in results:
                if isinstance(result, BaseException):
                    raise result
            return results[0]
        finally:
            self.refreshing = False
            for key in self.contracts:
                self.sync_model(key)
            await self.persist()
            self.changed()

    async def _fetch_tariff(self):
        if not self.provider.supports_tariff:
            return
        # Public requests must not race account login cookie updates.
        async with aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar()) as session:
            for key, item in self.saved["contracts"].items():
                region = self.options(key).get("tariff_region", default_region(self.provider))
                try:
                    tariff = await PortalClient(
                        session,
                        "",
                        "",
                        self.provider,
                        tariff_region=region,
                        tariff_profile=self.options(key).get("tariff_profile"),
                    ).tariff()
                    item["tariff"] = asdict(tariff)
                    item.pop("tariff_error", None)
                except GasError as error:
                    item.pop("tariff", None)
                    item["tariff_error"] = str(error)

    async def _fetch_data(self):
        errors = []
        for key, contract in self.contracts.items():
            item = self.saved["contracts"][key]
            self.progress[key] = "계약·자가검침 기간 확인 중…"
            self.changed()
            try:
                try:
                    await self.check_submission(key)
                except AuthenticationError:
                    raise
                except GasError as error:
                    item["meter_error"] = str(error)
                self.progress[key] = "최근 고지서 확인 중…"
                self.changed()

                def bill_progress(bills, completed, total, k=key, target=item):
                    target["bills"] = dict(bills)
                    self.progress[k] = (
                        f"고지서 이력 불러오는 중… {completed}/{total}개월 · 화면은 계속 사용할 수 있습니다."
                    )
                    self.changed()

                item["bills"] = await self.client.bills(
                    contract, item.get("bills", {}), progress=bill_progress
                )
                item["history_errors"] = self.client.history_errors.get(key, [])
                latest_bill = (
                    Bill.load(item["bills"][max(item["bills"])]) if item["bills"] else None
                )
                heat_start = (
                    (latest_bill.end + timedelta(days=1)).isoformat()
                    if latest_bill and latest_bill.end
                    else None
                )
                if (
                    self.provider.supports_tariff
                    and heat_start
                    and heat_start <= dt_util.now().date().isoformat()
                ):
                    self.progress[key] = "현재 기간 열량 확인 중…"
                    self.changed()
                    try:
                        item["caloric"] = await self.client.caloric(
                            heat_start, dt_util.now().date().isoformat()
                        )
                        item.pop("heat_error", None)
                    except GasError as error:
                        item["heat_error"] = str(error)
                item["last_refresh"] = dt_util.now().isoformat()
                item.pop("error", None)
            except AuthenticationError:
                item["error"] = "reauth_required"
                await self.persist()
                self.changed()
                raise ConfigEntryAuthFailed("reauth_required") from None
            except GasError as error:
                item["error"] = str(error)
                errors.append(str(error))
        await self.persist()
        self.changed()
        if errors:
            raise UpdateFailed(errors[0])
        return {"updated": dt_util.now().isoformat()}

    def view(self, key) -> dict:
        item = self.saved["contracts"][key]
        bills = sorted(
            [Bill.load(b) for b in item.get("bills", {}).values()], key=lambda b: b.month
        )
        window = self.windows.get(key)
        today = dt_util.now().date().isoformat()
        window_status = "unknown"
        if window and window.start and window.end and not item.get("meter_error"):
            if today < window.start:
                window_status = "before"
            elif today > window.end:
                window_status = "ended"
            else:
                window_status = "open" if window.eligible else "ineligible"
        tariff = Tariff.load(item["tariff"]) if item.get("tariff") else None
        values = forecast(
            self.estimates[key],
            bills,
            window,
            bool(self.options(key)["source_entity"]),
            dt_util.now(),
            tariff,
            item.get("caloric"),
            self.models[key],
        )
        estimate = self.estimates[key]
        cycle = item.get("submissions", {}).get(window.cycle, {}) if window else {}
        prior_year = next(
            (
                b
                for b in bills
                if bills and b.month == str(int(bills[-1].month[:4]) - 1) + bills[-1].month[4:]
            ),
            None,
        )
        return {
            "provider_id": self.provider.id,
            "provider_family": self.provider.family,
            "supports_submission": self.provider.supports_submission,
            "service_registration_required": bool(
                window and window.private.get("registered") is False
            ),
            "channel_change_required": bool(window and window.private.get("needs_channel_change")),
            "provider_name": self.provider.name,
            "provider_url": self.provider.billing_url,
            "tariff_profile": self.options(key).get("tariff_profile", "residential"),
            "entry_id": self.entry.entry_id,
            "key": key,
            "label": self.contracts[key].label,
            **values,
            "local_reading": estimate.value
            if self.options(key)["source_entity"]
            else values["reading"],
            "actual": estimate.actual,
            "actual_at": estimate.actual_at,
            "gap": estimate.gap,
            "source_configured": bool(self.options(key)["source_entity"]),
            "source_waiting": key in self.startup_deadlines,
            "billed_amount": bills[-1].amount if bills else None,
            "previous_amount": bills[-2].amount if len(bills) > 1 else None,
            "last_year_amount": prior_year.amount if prior_year else None,
            "billed_usage": str(bills[-1].usage) if bills and bills[-1].usage is not None else None,
            "billed_heat": str(bills[-1].heat) if bills and bills[-1].heat is not None else None,
            "due_date": bills[-1].due_date if bills else None,
            "last_refresh": item.get("last_refresh"),
            "error": item.get("error"),
            "tariff_error": item.get("tariff_error"),
            "heat_error": item.get("heat_error"),
            "meter_error": item.get("meter_error"),
            "history_errors": item.get("history_errors", []),
            "refreshing": self.refreshing,
            "refresh_progress": self.progress.get(key, "공식 정보 조회 준비 중…"),
            "submission_locked": not (SUBMISSION_ENABLED and self.provider.supports_submission),
            "automatic_submission": self.options(key)["automatic_submission"]
            and self.provider.supports_deadline,
            "supports_deadline": self.provider.supports_deadline,
            "automatic_submission_needs_confirmation": bool(
                self.entry.options.get("contracts", {}).get(key, {}).get("automatic_submission")
                and not self.options(key)["automatic_submission_confirmed"]
            ),
            "window_start": (window.start or None)
            if window and not window.private.get("dynamic_window")
            else None,
            "window_status": window_status,
            "today": today,
            "window_end": (window.end or None)
            if window and not window.private.get("dynamic_window")
            else None,
            "window_open": window_status == "open",
            "submission_status": cycle.get("status", "not_submitted"),
            "submission_error": cycle.get("error"),
            "submission_checking": key in self.receipt_tasks,
            "submission_attempted_at": cycle.get("attempted_at"),
            "submission_proposed": cycle.get("proposed"),
            "submission_observed": cycle.get("observed_reading"),
            "receipt_in_latest_read": cycle.get("receipt_in_latest_read"),
            "submission_blocked": bool(window and window.private.get("submission_blocked"))
            or key in self.startup_deadlines
            or cycle.get("status") in ("confirmed", "pending", "uncertain")
            or cycle.get("last_attempt_day") == today,
            "accepted": cycle.get("accepted"),
            "accepted_checked_at": cycle.get("checked_at"),
            "notification": {
                k: v for k, v in item.get("notification", {}).items() if k != "received_by"
            }
            | {"received_count": len(item.get("notification", {}).get("received_by", []))},
            "bills": [
                {
                    "month": b.month,
                    "amount": b.amount,
                    "usage": str(b.usage) if b.usage is not None else None,
                    "start": b.start.isoformat() if b.start else None,
                    "end": b.end.isoformat() if b.end else None,
                    "unsupported_adjustments": b.unsupported_adjustments,
                }
                for b in reversed(bills)
            ],
            "history": list(reversed(estimate.history)),
        }

    async def authorize(self, key, user_id):
        if not user_id:
            raise GasError("not_authorized")
        user = await self.hass.auth.async_get_user(user_id)
        if user is None or not user.is_active:
            raise GasError("not_authorized")
        selected = self.options(key)["recipients"]
        if not user.is_admin and user_id not in {
            p["user_id"] for k, p in phones(self.hass).items() if k in selected
        }:
            raise GasError("not_authorized")

    async def calibrate(
        self, key, value, *, physical=True, expected=None, accept_large=False, replace_meter=False
    ):
        async with self.action_lock:
            self.observe(key)
            estimate = deepcopy(self.estimates[key])
            source = self.options(key)["source_entity"]
            displayed = estimate.value if source else self.view(key)["local_reading"]
            if (
                expected is not None
                and displayed is not None
                and abs(decimal(expected) - decimal(displayed))
                > (decimal("0") if source else decimal("0.01"))
            ):
                raise GasError("stale_proposal")
            if not source:
                # Guard large corrections against the number the user sees.
                estimate.value = displayed if displayed is not None else estimate.value
            state = self.hass.states.get(source) if source else None
            now = dt_util.now()
            if state and state.attributes.get("restored"):
                raise GasError("source_waiting")
            estimate.calibrate(
                value,
                state.state if state else None,
                now,
                physical=physical,
                accept_large=accept_large,
                replace_meter=replace_meter,
            )
            self.estimates[key] = estimate
            if physical:
                self.startup_deadlines.pop(key, None)
            self.models[key].observe_physical(
                value,
                now,
                physical=physical,
                replace=replace_meter,
            )
            self.sync_model(key)
            await self.persist()
            self.prompts = {k: p for k, p in self.prompts.items() if p["key"] != key}
            self.submissions[key].proposals.clear()
            self.changed()

    def propose(self, key):
        if not self.provider.supports_submission:
            raise GasError("submission_disabled")
        view, window = self.view(key), self.windows.get(key)
        estimate = self.estimates[key]
        if not self.options(key)["source_entity"] and self.current_physical(key):
            if (dt_util.now() - datetime.fromisoformat(estimate.actual_at)).total_seconds() <= 1800:
                view["reading"], view["origin"] = estimate.actual, "manual"
        if window is None or view["reading"] is None:
            raise GasError("insufficient_data")
        return self.submissions[key].proposal(
            view["reading"], view["origin"], dt_util.now(), window
        )

    def current_physical(self, key):
        observations = self.models[key].state["observations"]
        estimate = self.estimates[key]
        return bool(
            observations
            and observations[-1]["at"] == estimate.actual_at
            and decimal(observations[-1]["value"]) == decimal(estimate.actual)
        )

    def validate_submission(self, key, proposal, window):
        self.observe(key)
        if key in self.startup_deadlines:
            raise GasError("source_waiting")
        view = self.view(key)
        if not self.options(key)["source_entity"] and proposal["origin"] == "manual":
            if not self.current_physical(key):
                raise GasError("physical_calibration_required")
            view["reading"] = self.estimates[key].actual
        if view["reading"] is None or int(decimal(view["reading"])) != proposal["value"]:
            raise GasError("stale_proposal")
        if self.options(key)["source_entity"]:
            if self.estimates[key].gap or self.estimates[key].actual is None:
                raise GasError("physical_calibration_required")
        elif (
            proposal["origin"] == "historical"
            and not self.options(key)["allow_historical_submission"]
        ):
            raise GasError("historical_submission_disabled")
        elif proposal["origin"] == "manual":
            if (
                not self.estimates[key].actual_at
                or datetime.fromisoformat(self.estimates[key].actual_at).date()
                != dt_util.now().date()
            ):
                raise GasError("physical_calibration_required")

    async def prepare_service(self, key, action, consent):
        if self.provider.family != "gasapp" or not consent:
            raise GasError("consent_required")
        async with self.action_lock:
            window = await self.client.prepare_service(self.contracts[key], action, consent)
            self.windows[key] = window
            self.saved["contracts"][key]["window"] = asdict(window)
            self.saved["contracts"][key].pop("meter_error", None)
            self.submissions[key].proposals.clear()
            self.sync_model(key)
            await self.persist()
            self.changed()
            return self.view(key)

    async def check_submission(self, key):
        """Coalesce concurrent receipt-only reads; leave invoices untouched."""
        if key not in self.receipt_tasks:

            async def check():
                try:
                    await self.submissions[key].check_receipt(dt_util.now())
                    self.saved["contracts"][key].pop("meter_error", None)
                except GasError as error:
                    self.saved["contracts"][key]["meter_error"] = str(error)
                    raise
                finally:
                    try:
                        await self.persist()
                    finally:
                        self.receipt_tasks.pop(key, None)
                        self.changed()
                return self.view(key)

            self.receipt_tasks[key] = self.hass.async_create_background_task(
                check(), f"{DOMAIN} receipt check", eager_start=False
            )
            self.changed()
        return await asyncio.shield(self.receipt_tasks[key])

    async def submit(self, key, proposal_id):
        async with self.action_lock:
            try:
                result = await self.submissions[key].submit(
                    proposal_id, dt_util.now(), lambda p, w: self.validate_submission(key, p, w)
                )
            finally:
                self.changed()  # Failed/uncertain writes must also update other phones.
            self.estimates[key].record("submitted", dt_util.now(), value=result.get("accepted"))
            await self.persist()
        try:
            await self.clear_notifications(key)
            await self.send_message(
                key, "가스 검침 제출 완료", f"접수된 값 {result.get('accepted')} m³"
            )
        except Exception:
            # The durable receipt must not become a submission failure just
            # because notification delivery or its bookkeeping failed.
            result = {**result, "notification_warning": "notification_failed"}
        finally:
            self.changed()
        return result

    def tag(self, key, kind="reading"):
        return f"busan-gas-{self.entry.entry_id}-{key}-{kind}"

    async def send_message(self, key, title, message, *, actions=None, kind="reading"):
        available = phones(self.hass)
        failures = 0
        sent = 0
        for recipient in self.options(key)["recipients"]:
            phone = available.get(recipient)
            if phone is None:
                failures += 1
                continue
            try:
                await self.hass.services.async_call(
                    "notify",
                    phone["service"],
                    {
                        "title": title,
                        "message": message,
                        "data": {
                            "tag": self.tag(key, kind),
                            "channel": "Gas meter reading",
                            "priority": "high",
                            "ttl": 0,
                            "confirmation": True,
                            "alert_once": False,
                            "clickAction": f"/{PANEL_PATH}?contract={key}",
                            "actions": actions or [],
                        },
                    },
                    blocking=True,
                )
                sent += 1
            except Exception:
                failures += 1
        self.saved["contracts"][key]["notification"] = {
            "requested_at": dt_util.now().isoformat(),
            "requested_count": sent,
            "failed_count": failures,
            "received_by": [],
        }
        await self.persist()
        self.changed()
        return {"requested": sent, "failed": failures}

    async def clear_notifications(self, key):
        for recipient in self.options(key)["recipients"]:
            if phone := phones(self.hass).get(recipient):
                for kind in ("reading", "submission"):
                    try:
                        await self.hass.services.async_call(
                            "notify",
                            phone["service"],
                            {"message": "clear_notification", "data": {"tag": self.tag(key, kind)}},
                            blocking=True,
                        )
                    except Exception:
                        pass

    async def prompt(self, key, kind="reading"):
        self.observe(key)
        view = self.view(key)
        reading = view["local_reading"] if kind == "reading" else view["reading"]
        if reading is None:
            if kind == "reading":
                return await self.send_message(
                    key,
                    "가스 검침 보정",
                    "계량기의 실제 숫자를 입력해 주세요.\n직접 입력하려면 알림을 누르시오.",
                )
            raise GasError("insufficient_data")
        token = secrets.token_urlsafe(24)
        self.prompts = {
            k: p
            for k, p in self.prompts.items()
            if p["key"] != key and p["expires"] > dt_util.now().isoformat()
        }
        self.prompts[token] = {
            "key": key,
            "kind": kind,
            "reading": reading,
            "expires": (dt_util.now() + timedelta(minutes=30)).isoformat(),
        }
        if kind == "submission":
            proposal = self.propose(key)
            self.prompts[token]["proposal_id"] = proposal["id"]
            buttons = [("submit", "제출하기"), ("open", "변경필요")]
            title, message = "가스 검침 제출", f"{proposal['value']} m³을 제출할까요?"
            if proposal["origin"] == "historical":
                message += " 과거 사용량·실측 기록 기반 추정값입니다."
            if not SUBMISSION_ENABLED:
                message += " 현재 제출 기능은 중지되어 있습니다."
        else:
            buttons = [("confirm", "맞음"), ("plus", "+0.1"), ("minus", "−0.1")]
            title = "가스 검침 보정"
            message = f"현재 추정값 {decimal(reading):.1f} m³ 같으면 '맞음' 다르면 ±0.1로 보정.\n직접 입력하려면 알림을 누르시오."
        actions = [
            {"action": "URI", "title": title, "uri": f"/{PANEL_PATH}?contract={key}"}
            if command == "open"
            else {
                "action": f"BCG:{token}:{command}",
                "title": title,
                "authenticationRequired": "true",
            }
            for command, title in buttons
        ]
        return await self.send_message(key, title, message, actions=actions, kind=kind)

    async def notification_action(self, event):
        parts = str(event.data.get("action", "")).split(":")
        if len(parts) != 3 or parts[0] != "BCG":
            return
        token, action = parts[1:]
        prompt = self.prompts.get(token)
        if prompt is None:
            return
        key = prompt["key"]
        try:
            await self.authorize(key, event.context.user_id)
            valid_phones = [
                p
                for k, p in phones(self.hass).items()
                if k in self.options(key)["recipients"] and p["user_id"] == event.context.user_id
            ]
            if not valid_phones or (
                event.data.get("device_id")
                and not any(p["device_id"] == event.data["device_id"] for p in valid_phones)
            ):
                raise GasError("not_authorized")
            if dt_util.now() >= datetime.fromisoformat(prompt["expires"]):
                self.prompts.pop(token, None)
                await self.prompt(key, prompt["kind"])
                return
            if action == "submit" and prompt["kind"] == "submission":
                await self.submit(key, prompt["proposal_id"])
            elif action in ("confirm", "plus", "minus") and prompt["kind"] == "reading":
                value = decimal(prompt["reading"])
                if action == "confirm":
                    value = decimal(f"{value:.1f}")  # Confirm what the phone displayed.
                elif action == "plus":
                    value += decimal("0.1")
                elif action == "minus":
                    value -= decimal("0.1")
                await self.calibrate(
                    key, value, physical=action == "confirm", expected=prompt["reading"]
                )
                if action == "confirm":
                    await self.clear_notifications(key)
                    await self.send_message(
                        key, "가스 검침 보정 완료", f"{value:.1f} m³로 보정했습니다."
                    )
                else:
                    await self.prompt(key)
        except GasError as error:
            if str(error) == "not_authorized":
                return
            await self.alert(key, str(error))

    async def notification_received(self, event):
        for key in self.contracts:
            if event.data.get("tag") not in (
                self.tag(key),
                self.tag(key, "submission"),
                self.tag(key, "test"),
            ):
                continue
            try:
                await self.authorize(key, event.context.user_id)
            except GasError:
                continue
            status = self.saved["contracts"][key].setdefault("notification", {})
            received = status.setdefault("received_by", [])
            # Store opaque user IDs privately; panel exposes only receipt count.
            if event.context.user_id not in received:
                received.append(event.context.user_id)
            status["received_at"] = dt_util.now().isoformat()
            await self.persist()
            self.changed()

    async def alert(self, key, code):
        messages = {
            "source_waiting": "센서 연결을 기다리고 있습니다. 연결이 확인되기 전에는 제출하지 않습니다.",
            "submission_disabled": "현재 제출 기능이 중지되어 있습니다. 홈페이지에서 직접 제출해 주세요.",
            "submission_uncertain": "접수 여부를 확정할 수 없습니다. 홈페이지에서 확인해 주세요. 중복 제출은 중지했습니다.",
            "submission_rejected": "서버가 저장 실패로 응답했고 재조회에서도 접수값을 확인하지 못했습니다. 원인은 제공되지 않았습니다. 오늘은 재전송하지 않습니다.",
            "submission_attempted_today": "오늘 이미 제출을 시도했습니다. 중복 전송을 막기 위해 오늘은 다시 보내지 않습니다. 홈페이지에서 접수 내역을 확인하세요.",
            "submission_value_mismatch": "조회된 접수값이 요청한 값과 다릅니다. 제출 성공으로 처리하지 않았으며 재전송을 중지했습니다. 홈페이지에서 확인하세요.",
            "submission_state_unknown": "검침 숫자가 조회되지만 접수 상태가 명확하지 않습니다. 홈페이지에서 확인하세요. 다시 보내지 않습니다.",
            "portal_reading_present": "이미 입력된 것으로 보이는 검침값이 있습니다. 덮어쓰지 않았습니다. 홈페이지에서 접수 상태를 확인하세요.",
            "submission_contract_changed": "계약 또는 계량기 정보가 달라 전송하지 않았습니다. 공식 정보를 새로고침하세요.",
            "submission_metadata_missing": "제출에 필요한 정보를 확인하지 못해 전송하지 않았습니다. 홈페이지에서 확인하세요.",
            "stale_proposal": "검침값이 바뀌었습니다. 화면을 새로 확인해 주세요.",
            "physical_calibration_required": "계량기의 실제 숫자를 확인하고 보정해 주세요.",
            "insufficient_data": "검침 추정 자료가 부족합니다. 실제 검침값을 입력해 주세요.",
        }
        message = messages.get(
            code,
            "처리 결과를 확인하지 못했습니다. 홈페이지에서 접수 상태를 먼저 확인하고 확인 없이 다시 전송하지 마세요.",
        )
        persistent_notification.async_create(
            self.hass, message, f"{self.provider.name} 확인 필요", self.tag(key, "error")
        )
        await self.send_message(key, "가스 검침 확인 필요", message, kind="error")

    async def tick(self, now):
        if self._closing or self.tick_lock.locked():
            return
        async with self.tick_lock:
            local = dt_util.as_local(now)
            for key in self.contracts:
                self.observe(key)
                self.sync_model(key)
                opts = self.options(key)
                data = self.saved["contracts"][key]
                marks = data.setdefault("schedule", {})
                date_key = local.date().isoformat()
                minute = local.strftime("%H:%M")
                if (
                    opts["weekly_enabled"]
                    and local.weekday() == int(opts["weekly_day"])
                    and minute == opts["weekly_time"][:5]
                    and marks.get("weekly") != date_key
                ):
                    marks["weekly"] = date_key
                    await self.persist()
                    try:
                        await self.prompt(key)
                    except GasError as error:
                        await self.alert(key, str(error))
                window = self.windows.get(key)
                if window is None or window.private.get("dynamic_window"):
                    continue
                if data.get("submissions", {}).get(window.cycle, {}).get("status") == "confirmed":
                    continue
                reminder = (
                    opts["reminder_enabled"]
                    and window.start <= date_key < window.end
                    and minute == opts["reminder_time"][:5]
                    and marks.get("reminder") != date_key
                )
                deadline = (
                    opts["automatic_submission"]
                    and date_key == window.end
                    and minute >= opts["deadline_time"][:5]
                    and marks.get("deadline") != window.cycle
                )
                if not (reminder or deadline):
                    continue
                # Never catch up as an immediate side effect of installing/options
                # reload. Wait one scheduler pass (>= 60 seconds), then re-query.
                if (local - self.ready_at).total_seconds() < 60:
                    continue
                mark = "deadline" if deadline else "reminder"
                try:
                    fresh = await self.submissions[key].query()
                    if fresh.cycle != window.cycle or not fresh.is_open(local.date()):
                        raise GasError("window_closed")
                    if await self.submissions[key].reconcile(fresh, local):
                        await self.clear_notifications(key)
                        continue
                    if deadline:
                        await self.submit(key, self.propose(key)["id"])
                    else:
                        await self.prompt(key, "submission")
                except GasError as error:
                    await self.alert(key, str(error))
                # The submission manager persists its own write-ahead intent.
                # Do not suppress restart catch-up merely because a scheduler
                # started a query and crashed before attempting the operation.
                marks[mark] = window.cycle if deadline else date_key
                await self.persist()
            await self.persist()
            self.changed()

    async def shutdown(self):
        self._closing = True
        if self.initial_refresh_task and not self.initial_refresh_task.done():
            self.initial_refresh_task.cancel()
            await asyncio.gather(self.initial_refresh_task, return_exceptions=True)
        await self.async_shutdown()
        checks = list(self.receipt_tasks.values())
        for task in checks:
            task.cancel()
        await asyncio.gather(*checks, return_exceptions=True)
        for remove in self.removers:
            remove()
        await self.persist()
        await self.session.close()
