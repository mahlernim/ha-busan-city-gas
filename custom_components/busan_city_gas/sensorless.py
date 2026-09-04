"""Checkpointed seasonal/physical model; no I/O and no synthetic observations."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
from decimal import Decimal

from .model import Bill, Estimate, MeterWindow, decimal


def days_between(start: datetime, end: datetime) -> Decimal:
    # POSIX timestamps keep elapsed time correct across local DST transitions.
    return Decimal(str(end.timestamp() - start.timestamp())) / 86400


def recent_rate(observations: list[dict]) -> tuple[str | None, str, str | None]:
    if not observations:
        return None, "0", None
    last = observations[-1]
    end = datetime.fromisoformat(last["at"])
    for first in observations[:-1]:
        elapsed = days_between(datetime.fromisoformat(first["at"]), end)
        if 7 <= elapsed <= 28:
            delta = decimal(last["value"]) - decimal(first["value"])
            if delta >= 0:
                return str(delta / elapsed), str(elapsed), last["at"]
    return None, "0", last["at"]


def rate_at(policy: dict, now: datetime) -> tuple[Decimal | None, str, Decimal]:
    try:
        previous = now.date().replace(year=now.year - 1)
    except ValueError:  # February 29 maps to February 28, not a missing day.
        previous = now.date().replace(year=now.year - 1, day=28)
    historical = next(
        (
            decimal(row["daily"])
            for row in reversed(policy.get("periods", []))
            if row["start"] <= previous.isoformat() <= row["end"]
        ),
        None,
    )
    recent = policy.get("recent")
    age = (
        days_between(datetime.fromisoformat(policy["last_actual_at"]), now)
        if policy.get("last_actual_at")
        else Decimal(99)
    )
    weight = (
        min(Decimal("0.5"), max(Decimal(0), (28 - age) / 28))
        if recent is not None and age >= 0
        else Decimal(0)
    )
    if historical is not None:
        return (
            (1 - weight) * historical + weight * decimal(recent or "0"),
            "historical_blend" if weight else "historical",
            weight,
        )
    if recent is not None and 0 <= age <= 14:
        return decimal(recent), "recent_physical", Decimal(1)
    return None, "insufficient_data", Decimal(0)


def integrate(policy: dict, start: datetime, end: datetime) -> Decimal | None:
    if end < start or days_between(start, end) > 3660:
        return None
    total = Decimal(0)
    cursor = start
    knots = []
    if policy.get("last_actual_at"):
        actual = datetime.fromisoformat(policy["last_actual_at"])
        knots = [actual + timedelta(days=14), actual + timedelta(days=28)]
    while cursor < end:
        midnight = datetime.combine(
            cursor.date() + timedelta(days=1), datetime.min.time(), cursor.tzinfo
        )
        stop = min([end, midnight] + [point for point in knots if point > cursor])
        midpoint = datetime.fromtimestamp(
            (cursor.timestamp() + stop.timestamp()) / 2, cursor.tzinfo
        )
        rate, _, _ = rate_at(policy, midpoint)
        if rate is None:
            return None  # Never zero-fill unobserved time.
        total += rate * days_between(cursor, stop)
        cursor = stop
    return total


class SensorlessModel:
    """Separate storage area preserves compatibility with Estimate(**saved)."""

    def __init__(self, saved: dict | None = None):
        self.state = (
            deepcopy(saved)
            if saved
            else {
                "version": 1,
                "meter": None,
                "observations": [],
                "checkpoint": None,
                "policy": {},
                "initialized": False,
            }
        )

    def reading(self, now: datetime) -> Decimal | None:
        anchor = self.state.get("checkpoint")
        if not anchor:
            return None
        delta = integrate(self.state["policy"], datetime.fromisoformat(anchor["at"]), now)
        return None if delta is None else decimal(anchor["value"]) + delta

    def observe_physical(self, value, now: datetime, *, physical=True, replace=False):
        if replace:
            self.state["observations"] = []
        if physical:
            observations = [
                item
                for item in self.state["observations"]
                if now.date() - timedelta(days=90)
                <= datetime.fromisoformat(item["at"]).astimezone(now.tzinfo).date()
                < now.date()
            ]
            observations.append({"at": now.isoformat(), "value": str(decimal(value))})
            self.state["observations"] = observations
        self.state["checkpoint"] = {"at": now.isoformat(), "value": str(decimal(value))}
        self.state["anchor_kind"] = "physical" if physical else "adjustment"

    def sync(
        self, bills: list[Bill], window: MeterWindow | None, estimate: Estimate, now: datetime
    ):
        meter = window.meter if window else None
        if meter and self.state["meter"] and meter != self.state["meter"]:
            self.state.update(observations=[], checkpoint=None, policy={}, anchor_kind=None)
            # Do not seed the old physical observation into a replacement meter.
            self.state["initialized"] = True
        if meter:
            self.state["meter"] = meter
        if not self.state["initialized"]:
            if estimate.actual is not None and estimate.actual_at:
                at = datetime.fromisoformat(estimate.actual_at)
                meter_change = any(
                    row.get("kind") == "physical_meter_changed"
                    and datetime.fromisoformat(row["at"]) > at
                    for row in estimate.history
                )
                if at <= now and not meter_change:
                    self.observe_physical(estimate.actual, at)
            self.state["initialized"] = True

        # A dated bill is an anchor; an undated portal 'previous' value is not.
        matching = [
            b
            for b in bills
            if meter
            and b.meter == meter
            and b.last_reading is not None
            and b.end
            and b.end < now.date()
        ]
        if matching:
            latest = max(matching, key=lambda b: b.end)
            at = datetime.combine(latest.end + timedelta(days=1), datetime.min.time(), now.tzinfo)
            anchor = self.state["checkpoint"]
            if not anchor:
                self.state["checkpoint"] = {
                    "at": at.isoformat(),
                    "value": latest.last_reading,
                }
                self.state["anchor_kind"] = "official"

        cutoff = now.date() - timedelta(days=90)
        self.state["observations"] = [
            o
            for o in self.state["observations"]
            if datetime.fromisoformat(o["at"]).astimezone(now.tzinfo).date() >= cutoff
        ]
        recent, elapsed, actual_at = recent_rate(self.state["observations"])
        policy = {
            "periods": [
                {
                    "start": b.start.isoformat(),
                    "end": b.end.isoformat(),
                    "daily": str(b.usage / ((b.end - b.start).days + 1)),
                }
                for b in sorted(
                    (
                        b
                        for b in bills
                        if b.start and b.end and b.usage is not None and b.start <= b.end
                    ),
                    key=lambda b: (b.end, b.month),
                )
            ],
            "recent": recent,
            "learning_days": elapsed,
            "last_actual_at": actual_at,
        }
        if policy != self.state["policy"]:
            value = self.reading(now)
            if value is not None:
                self.state["checkpoint"] = {"at": now.isoformat(), "value": str(value)}
            self.state["policy"] = policy

    def snapshot(self, now: datetime) -> dict:
        value = self.reading(now)
        rate, method, weight = rate_at(self.state["policy"], now)
        status = "ready"
        if not self.state["checkpoint"]:
            status = "anchor_required"
        elif value is None or rate is None:
            status = "insufficient_data"
        elif self.state["policy"].get("recent") is None:
            status = "learning_pending"
        return {
            "reading": str(value) if value is not None else None,
            "origin": "historical",  # Preserve the submission-consent gate.
            "estimation_method": method,
            "estimated_daily_usage": str(rate) if rate is not None else None,
            "learning_days": self.state["policy"].get("learning_days", "0"),
            "recent_weight": str(weight),
            "estimation_status": status,
        }

    def future_usage(self, start: datetime, end: datetime) -> Decimal | None:
        return integrate(self.state["policy"], start, end)
