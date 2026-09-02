"""Durable submission state machine. Injectable transport permits offline tests."""

from __future__ import annotations

import asyncio
import secrets
from datetime import datetime, timedelta
from typing import Awaitable, Callable

from .model import GasError, MeterWindow, decimal
from .submission_transport import SubmissionNotSent, SubmissionRejected


class SubmissionManager:
    def __init__(
        self,
        state: dict,
        persist: Callable[[], Awaitable[None]],
        query: Callable[[], Awaitable[MeterWindow]],
        write: Callable[[MeterWindow, int], Awaitable[None]],
        *,
        enabled: bool = True,
    ):
        self.state, self.persist, self.query, self.write = state, persist, query, write
        self.enabled = enabled
        self.lock = asyncio.Lock()
        self.proposals: dict[str, dict] = {}

    def proposal(self, reading: str, origin: str, now: datetime, window: MeterWindow) -> dict:
        item = {
            "id": secrets.token_urlsafe(24),
            "value": int(decimal(reading)),
            "origin": origin,
            "cycle": window.cycle,
            "expires": (now + timedelta(minutes=30)).isoformat(),
        }
        self.proposals = {
            key: value
            for key, value in self.proposals.items()
            if value["expires"] > now.isoformat()
        }
        self.proposals[item["id"]] = item
        return dict(item)

    async def reconcile(self, window: MeterWindow, now: datetime) -> bool:
        cycle = self.state.setdefault(window.cycle, {})
        cycle.update(
            checked_at=now.isoformat(),
            observed_reading=window.submitted or window.private.get("reported_reading"),
            receipt_in_latest_read=window.submitted is not None,
        )
        if window.submitted is not None:
            if cycle.get("proposed") is not None and decimal(window.submitted) != cycle["proposed"]:
                cycle.update(
                    status="uncertain",
                    accepted=window.submitted,
                    checked_at=now.isoformat(),
                    error="submission_value_mismatch",
                )
                self.state[window.cycle] = cycle
                await self.persist()
                return False
            cycle.update(status="confirmed", accepted=window.submitted, checked_at=now.isoformat())
            cycle.pop("error", None)
            self.state[window.cycle] = cycle
            await self.persist()
            return True
        # Do not erase a successful or ambiguous earlier result when a later
        # page temporarily omits the submission field.
        if cycle.get("status") == "pending":
            cycle.update(status="uncertain", error="submission_uncertain")
        await self.persist()
        return cycle.get("status") == "confirmed"

    async def check_receipt(self, now: datetime) -> MeterWindow:
        """Read/reconcile only, also when writes are locked. Never calls write."""
        async with self.lock:
            window = await self.query()
            await self.reconcile(window, now)
            return window

    async def submit(
        self, proposal_id: str, now: datetime, validate: Callable[[dict, MeterWindow], None]
    ) -> dict:
        async with self.lock:
            if now.tzinfo is None:
                raise GasError("invalid_submission_time")
            proposal = self.proposals.get(proposal_id)
            if proposal is None or now >= datetime.fromisoformat(proposal["expires"]):
                raise GasError("stale_proposal")
            if not self.enabled:
                raise GasError("submission_disabled")
            prior = self.state.get(proposal["cycle"], {})
            try:
                window = await self.query()
            except GasError:
                if prior.get("status") in ("pending", "uncertain"):
                    raise GasError("submission_uncertain") from None
                raise
            if window.cycle != proposal["cycle"]:
                raise GasError("window_closed")
            if await self.reconcile(window, now):
                return self.state[window.cycle]
            if not window.is_open(now.date()):
                raise GasError("window_closed")
            cycle = self.state.setdefault(window.cycle, {})
            if cycle.get("status") in ("pending", "uncertain"):
                raise GasError(cycle.get("error") or "submission_uncertain")
            if window.private.get("submission_blocked"):
                raise GasError("portal_reading_present")
            if cycle.get("last_attempt_day") == now.date().isoformat():
                raise GasError("submission_attempted_today")
            validate(proposal, window)
            if proposal["value"] < decimal(window.previous):
                raise GasError("below_official_reading")
            cycle.update(
                status="pending",
                attempted_at=now.isoformat(),
                proposed=proposal["value"],
                origin=proposal["origin"],
                last_attempt_day=now.date().isoformat(),
            )
            cycle.pop("error", None)
            await self.persist()  # Crash-safe write-ahead intent, before network.
            write_error = None
            try:
                await self.write(window, proposal["value"])
            except SubmissionNotSent as error:
                cycle.update(status="not_sent", error=str(error))
                cycle.pop("last_attempt_day", None)
                await self.persist()
                raise GasError(str(error)) from None
            except asyncio.CancelledError:
                cycle.update(status="uncertain", error="submission_uncertain")
                await self.persist()
                raise
            except Exception as error:
                write_error = error
            # Even a lost acknowledgement may have committed. Query once, never
            # re-POST. Keep pending/uncertain durable if this task is interrupted.
            cycle.update(status="uncertain", error="submission_uncertain")
            try:
                latest = await self.query()
                if latest.cycle == window.cycle:
                    if latest.submitted is not None:
                        await self.reconcile(latest, now)
                    elif latest.private.get("submission_blocked"):
                        cycle.update(
                            error="submission_state_unknown",
                            observed_reading=latest.private.get("reported_reading"),
                        )
                    elif isinstance(write_error, SubmissionRejected):
                        cycle.update(status="rejected", error="submission_rejected")
            except asyncio.CancelledError:
                raise
            except Exception:
                pass  # No acknowledgement/readback details may leak into logs.
            finally:
                await self.persist()
            if cycle["status"] != "confirmed":
                raise GasError(cycle["error"])
            # Retain short-lived proposals so concurrent confirmations resolve
            # to the same already-confirmed result, rather than a second write.
            return dict(cycle)
