"""Durable submission state machine. Injectable transport permits offline tests."""

from __future__ import annotations

import asyncio
import secrets
from datetime import datetime, timedelta
from typing import Awaitable, Callable

from .model import GasError, MeterWindow, decimal
from .submission_transport import (
    SubmissionAcknowledgement,
    SubmissionNotSent,
    SubmissionRejected,
)


class SubmissionManager:
    def __init__(
        self,
        state: dict,
        persist: Callable[[], Awaitable[None]],
        query: Callable[[], Awaitable[MeterWindow]],
        write: Callable[..., Awaitable[SubmissionAcknowledgement | None]],
        *,
        enabled: bool = True,
        supports_revision: bool = False,
    ):
        self.state, self.persist, self.query, self.write = state, persist, query, write
        self.enabled = enabled
        self.supports_revision = supports_revision
        self.lock = asyncio.Lock()
        self.proposals: dict[str, dict] = {}

    def proposal(
        self,
        reading: str,
        origin: str,
        now: datetime,
        window: MeterWindow,
        *,
        revision: bool = False,
        revision_from: str | None = None,
        revision_from_at: str | None = None,
        revision_from_at_kind: str | None = None,
    ) -> dict:
        if revision and not self.supports_revision:
            raise GasError("revision_submission_unsupported")
        if revision and revision_from is None:
            raise GasError("revision_submission_unavailable")
        item = {
            "id": secrets.token_urlsafe(24),
            "value": int(decimal(reading)),
            "origin": origin,
            "cycle": window.cycle,
            "expires": (now + timedelta(minutes=30)).isoformat(),
            "revision": revision,
        }
        if revision:
            item.update(
                revision_from=str(decimal(revision_from)),
                revision_from_at=revision_from_at,
                revision_from_at_kind=revision_from_at_kind,
            )
        self.proposals = {
            key: value
            for key, value in self.proposals.items()
            if value["expires"] > now.isoformat()
        }
        self.proposals[item["id"]] = item
        return dict(item)

    @staticmethod
    def _same_integer(left, right) -> bool:
        return int(decimal(left)) == int(decimal(right))

    @staticmethod
    def _record_revision(cycle: dict, now: datetime, source: str) -> None:
        previous = cycle.get("revision_from")
        proposed = cycle.get("proposed")
        if previous is None or proposed is None:
            return
        entry = {
            "from": str(previous),
            "to": str(proposed),
            "attempted_at": cycle.get("attempted_at"),
            "confirmed_at": now.isoformat(),
            "confirmation_source": source,
        }
        revisions = cycle.setdefault("revisions", [])
        identity = (entry["from"], entry["to"], entry["attempted_at"])
        if (
            not revisions
            or (
                revisions[-1].get("from"),
                revisions[-1].get("to"),
                revisions[-1].get("attempted_at"),
            )
            != identity
        ):
            revisions.append(entry)
            del revisions[:-20]
        cycle["last_revision"] = entry

    async def reconcile(self, window: MeterWindow, now: datetime) -> bool:
        cycle = self.state.setdefault(window.cycle, {})
        cycle.update(
            checked_at=now.isoformat(),
            observed_reading=window.submitted or window.private.get("reported_reading"),
            receipt_in_latest_read=window.submitted is not None,
        )
        if window.submitted is not None:
            if cycle.get("proposed") is not None and decimal(window.submitted) != cycle["proposed"]:
                stale_revision_values = {
                    str(value)
                    for value in (
                        cycle.get("revision_from"),
                        cycle.get("revision_observed_before"),
                    )
                    if value is not None
                }
                if (
                    cycle.get("status") == "confirmed"
                    and cycle.get("confirmation_source") == "provider_response"
                    and any(
                        decimal(window.submitted) == decimal(value)
                        for value in stale_revision_values
                    )
                ):
                    # A successful revision response can become visible before the
                    # meter read endpoint catches up. Preserve the confirmed write,
                    # but make the lag explicit until a matching readback arrives.
                    cycle.update(
                        checked_at=now.isoformat(),
                        observed_reading=window.submitted,
                        receipt_in_latest_read=False,
                        revision_readback_pending=True,
                    )
                    self.state[window.cycle] = cycle
                    await self.persist()
                    return True
                cycle.update(
                    status="uncertain",
                    accepted=window.submitted,
                    checked_at=now.isoformat(),
                    error="submission_value_mismatch",
                )
                self.state[window.cycle] = cycle
                await self.persist()
                return False
            cycle.update(
                status="confirmed",
                accepted=window.submitted,
                checked_at=now.isoformat(),
                confirmation_source="readback",
            )
            confirmed_revision = cycle.get("revision_from") is not None
            self._record_revision(cycle, now, "readback")
            cycle.pop("revision_readback_pending", None)
            cycle.pop("revision_from", None)
            cycle.pop("revision_observed_before", None)
            if confirmed_revision:
                cycle.pop("revision_rejected_day", None)
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
            is_revision = bool(proposal.get("revision"))
            if is_revision and not self.supports_revision:
                raise GasError("revision_submission_unsupported")
            prior = self.state.get(proposal["cycle"], {})
            try:
                window = await self.query()
            except GasError:
                if prior.get("status") in ("pending", "uncertain"):
                    raise GasError("submission_uncertain") from None
                raise
            if window.cycle != proposal["cycle"]:
                raise GasError("window_closed")
            cycle = self.state.setdefault(window.cycle, {})
            if is_revision:
                if not window.is_open(now.date()):
                    raise GasError("window_closed")
                sealed_from = proposal.get("revision_from")
                if sealed_from is None:
                    raise GasError("revision_submission_unavailable")
                if window.submitted is not None and self._same_integer(
                    window.submitted, proposal["value"]
                ):
                    # Another actor already reached the explicitly confirmed
                    # target. Resolve without a second POST.
                    cycle.update(
                        status="confirmed",
                        accepted=window.submitted,
                        proposed=proposal["value"],
                        origin=proposal["origin"],
                        checked_at=now.isoformat(),
                        observed_reading=window.submitted,
                        receipt_in_latest_read=True,
                        confirmation_source="readback",
                        revision_resolved_at=now.isoformat(),
                        revision_idempotent=True,
                    )
                    cycle.pop("error", None)
                    cycle.pop("revision_readback_pending", None)
                    cycle.pop("revision_from", None)
                    cycle.pop("revision_observed_before", None)
                    cycle.pop("revision_rejected_day", None)
                    await self.persist()
                    return dict(cycle)
                if window.submitted is not None and decimal(window.submitted) != decimal(
                    sealed_from
                ):
                    # The value confirmed by the user is no longer current. Save
                    # the new authoritative receipt, but require a new proposal
                    # and confirmation before any write.
                    cycle.update(
                        status="confirmed",
                        accepted=window.submitted,
                        proposed=int(decimal(window.submitted)),
                        checked_at=now.isoformat(),
                        observed_reading=window.submitted,
                        receipt_in_latest_read=True,
                        confirmation_source="readback",
                    )
                    cycle.pop("error", None)
                    cycle.pop("revision_readback_pending", None)
                    cycle.pop("revision_from", None)
                    cycle.pop("revision_observed_before", None)
                    await self.persist()
                    raise GasError("revision_state_changed")
                if window.submitted is None and (
                    cycle.get("status") != "confirmed"
                    or cycle.get("confirmation_source") != "provider_response"
                    or cycle.get("accepted") is None
                    or decimal(cycle["accepted"]) != decimal(sealed_from)
                ):
                    raise GasError("revision_submission_unavailable")
            already_confirmed = await self.reconcile(window, now)
            if already_confirmed and not is_revision:
                return self.state[window.cycle]
            if not window.is_open(now.date()):
                raise GasError("window_closed")
            if cycle.get("status") in ("pending", "uncertain"):
                raise GasError(cycle.get("error") or "submission_uncertain")
            revision_from = None
            if is_revision:
                if cycle.get("status") != "confirmed":
                    raise GasError("revision_submission_unavailable")
                revision_from = proposal["revision_from"]
                if cycle.get("accepted") is None or decimal(cycle["accepted"]) != decimal(
                    revision_from
                ):
                    raise GasError("revision_state_changed")
                if self._same_integer(revision_from, proposal["value"]):
                    raise GasError("submission_value_unchanged")
                if cycle.get("revision_rejected_day") == now.date().isoformat():
                    raise GasError("submission_attempted_today")
            if window.private.get("submission_blocked"):
                raise GasError("portal_reading_present")
            if not is_revision and cycle.get("last_attempt_day") == now.date().isoformat():
                raise GasError("submission_attempted_today")
            validate(proposal, window)
            if proposal["value"] < decimal(window.previous):
                raise GasError("below_official_reading")
            prior_cycle = dict(cycle)
            revision_observed_before = window.submitted if is_revision else None
            cycle.update(
                status="pending",
                attempted_at=now.isoformat(),
                proposed=proposal["value"],
                origin=proposal["origin"],
                last_attempt_day=now.date().isoformat(),
            )
            if is_revision:
                cycle.update(
                    revision_from=str(revision_from),
                    revision_observed_before=revision_observed_before,
                )
                cycle.pop("last_revision_error", None)
            cycle.pop("error", None)
            await self.persist()  # Crash-safe write-ahead intent, before network.
            write_error = None
            acknowledgement = None
            try:
                if is_revision:
                    acknowledgement = await self.write(
                        window,
                        proposal["value"],
                        revision=True,
                        revision_from=revision_from,
                    )
                else:
                    acknowledgement = await self.write(window, proposal["value"])
            except SubmissionNotSent as error:
                if is_revision:
                    failed = {
                        "from": str(revision_from),
                        "to": str(proposal["value"]),
                        "attempted_at": now.isoformat(),
                        "error": str(error),
                    }
                    attempts = list(prior_cycle.get("revision_attempts", []))
                    attempts.append(failed)
                    cycle.clear()
                    cycle.update(prior_cycle)
                    cycle["revision_attempts"] = attempts[-20:]
                    cycle["last_revision_error"] = str(error)
                else:
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
            if isinstance(acknowledgement, SubmissionAcknowledgement):
                cycle.update(
                    status="confirmed",
                    accepted=str(proposal["value"]),
                    confirmation_source="provider_response",
                    acknowledgement_matcher=acknowledgement.matcher,
                    acknowledged_at=now.isoformat(),
                )
                if is_revision:
                    cycle["revision_readback_pending"] = True
                    cycle.pop("revision_rejected_day", None)
                    self._record_revision(cycle, now, "provider_response")
                cycle.pop("error", None)
            else:
                # Even a lost or unknown acknowledgement may have committed. Query
                # once, never re-POST, and retain the durable ambiguous state.
                cycle.update(status="uncertain", error="submission_uncertain")
            terminal_error = None
            try:
                latest = await self.query()
                if latest.cycle == window.cycle:
                    if latest.submitted is not None:
                        if (
                            is_revision
                            and isinstance(write_error, SubmissionRejected)
                            and any(
                                self._same_integer(latest.submitted, value)
                                for value in (revision_from, revision_observed_before)
                                if value is not None
                            )
                        ):
                            # A definite rejection plus unchanged readback means
                            # the earlier confirmed receipt is still authoritative.
                            attempts = list(prior_cycle.get("revision_attempts", []))
                            attempts.append(
                                {
                                    "from": str(revision_from),
                                    "to": str(proposal["value"]),
                                    "attempted_at": now.isoformat(),
                                    "error": "submission_rejected",
                                }
                            )
                            cycle.clear()
                            cycle.update(prior_cycle)
                            cycle["revision_attempts"] = attempts[-20:]
                            cycle["last_revision_error"] = "submission_rejected"
                            cycle["revision_rejected_day"] = now.date().isoformat()
                            terminal_error = "submission_rejected"
                        else:
                            await self.reconcile(latest, now)
                    elif (
                        is_revision
                        and isinstance(write_error, SubmissionRejected)
                        and revision_observed_before is None
                        and prior_cycle.get("status") == "confirmed"
                        and prior_cycle.get("confirmation_source") == "provider_response"
                    ):
                        attempts = list(prior_cycle.get("revision_attempts", []))
                        attempts.append(
                            {
                                "from": str(revision_from),
                                "to": str(proposal["value"]),
                                "attempted_at": now.isoformat(),
                                "error": "submission_rejected",
                            }
                        )
                        cycle.clear()
                        cycle.update(prior_cycle)
                        cycle["revision_attempts"] = attempts[-20:]
                        cycle["last_revision_error"] = "submission_rejected"
                        cycle["revision_rejected_day"] = now.date().isoformat()
                        terminal_error = "submission_rejected"
                    elif latest.private.get("submission_blocked") and not isinstance(
                        acknowledgement, SubmissionAcknowledgement
                    ):
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
            if terminal_error:
                raise GasError(terminal_error)
            if cycle["status"] != "confirmed":
                raise GasError(cycle["error"])
            # Retain short-lived proposals so concurrent confirmations resolve
            # to the same already-confirmed result, rather than a second write.
            return dict(cycle)
