"""Pure calculation layer; no network, Home Assistant or credential dependencies."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from typing import Any


class GasError(Exception):
    """A safe, machine-readable error code (never a raw server response)."""


def decimal(value: Any) -> Decimal:
    try:
        result = Decimal(str(value).replace(",", ""))
    except (ValueError, InvalidOperation):
        raise GasError("invalid_number") from None
    if not result.is_finite() or result < 0:
        raise GasError("invalid_number")
    return result


def trunc(value: Decimal, places: int = 0) -> Decimal:
    return value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_DOWN)


@dataclass
class Segment:
    start: str
    end: str
    previous: str
    current: str
    coefficient: str
    heat_factor: str
    meter: str = ""

    @property
    def usage(self) -> Decimal:
        result = decimal(self.current) - decimal(self.previous)
        if result < 0:
            raise GasError("invalid_bill_segment")
        return result

    @property
    def heat(self) -> Decimal:
        return trunc(self.usage * decimal(self.coefficient) * decimal(self.heat_factor), 4)


@dataclass
class Bill:
    month: str
    amount: str
    segments: list[Segment]
    base_charge: str = "900"
    price_lines: list[tuple[str, str]] = field(default_factory=list)
    due_date: str | None = None
    unsupported_adjustments: bool = False

    @property
    def usage(self) -> Decimal:
        return sum((s.usage for s in self.segments), Decimal(0))

    @property
    def heat(self) -> Decimal:
        return sum((s.heat for s in self.segments), Decimal(0))

    @property
    def start(self) -> date:
        return date.fromisoformat(self.segments[0].start)

    @property
    def end(self) -> date:
        return date.fromisoformat(self.segments[-1].end)

    def reconstructed(self) -> Decimal:
        subtotal = decimal(self.base_charge) + sum(
            (trunc(decimal(heat) * decimal(price)) for heat, price in self.price_lines),
            Decimal(0),
        )
        return trunc((subtotal + trunc(subtotal / 10)) / 10) * 10

    def dump(self) -> dict:
        return asdict(self)

    @classmethod
    def load(cls, data: dict) -> Bill:
        return cls(**{**data, "segments": [Segment(**s) for s in data["segments"]]})


@dataclass
class Tariff:
    effective: str
    first_rate: str
    second_rate: str
    threshold_mj: str = "516"

    def estimate(self, usage: Decimal, coefficient: str, heat_factor: str, base: str) -> Decimal:
        heat = trunc(usage * decimal(coefficient) * decimal(heat_factor), 4)
        boundary = decimal(self.threshold_mj)
        subtotal = (
            decimal(base)
            + trunc(min(heat, boundary) * decimal(self.first_rate))
            + trunc(max(Decimal(0), heat - boundary) * decimal(self.second_rate))
        )
        return trunc((subtotal + trunc(subtotal / 10)) / 10) * 10


@dataclass
class MeterWindow:
    start: str
    end: str
    previous: str
    meter: str
    planned: str | None = None
    eligible: bool = False
    submitted: str | None = None
    # Raw identification is private to the transport, never exposed by snapshot.
    private: dict = field(default_factory=dict, repr=False)

    @property
    def cycle(self) -> str:
        return f"{self.meter}:{self.start}:{self.end}"

    def is_open(self, today: date) -> bool:
        return self.eligible and date.fromisoformat(self.start) <= today <= date.fromisoformat(
            self.end
        )


@dataclass
class Estimate:
    """Atomic checkpoint: estimate, source cursor, last physical anchor and day bins."""

    value: str | None = None
    source_last: str | None = None
    source_at: str | None = None
    actual: str | None = None
    actual_at: str | None = None
    gap: bool = True
    days: dict[str, dict] = field(default_factory=dict)
    history: list[dict] = field(default_factory=list)

    def record(self, kind: str, now: datetime, **values: Any) -> None:
        self.history.append({"kind": kind, "at": now.isoformat(), **values})
        self.history = self.history[-200:]

    def invalidate(self, now: datetime) -> None:
        self.gap = True
        day = self.days.setdefault(now.date().isoformat(), {"volume": "0", "complete": False})
        day.update(complete=False, invalid=True)

    def observe(self, raw: Any, now: datetime) -> None:
        try:
            current = decimal(raw)
        except GasError:
            self.invalidate(now)
            return
        today = now.date().isoformat()
        self.days.setdefault(today, {"volume": "0", "complete": False})
        if self.source_last is not None and self.source_at is not None:
            previous = decimal(self.source_last)
            prior_at = datetime.fromisoformat(self.source_at)
            delta = current - previous
            if delta < 0:
                # A reset can hide pre-reset pulses. Preserve visible post-reset
                # usage, but require physical calibration before unattended writes.
                delta = current
                self.invalidate(now)
                self.record("source_reset", now)
            if self.value is not None:
                self.value = str(decimal(self.value) + delta)
            if prior_at.date() != now.date():
                # Intervals crossing midnight cannot be assigned to a day unless
                # a midnight checkpoint observed the same source value.
                previous_day = self.days.setdefault(prior_at.date().isoformat(), {"volume": "0"})
                previous_day["complete"] = (
                    previous_day.get("started", False)
                    and not previous_day.get("invalid", False)
                    and not self.gap
                    and delta == 0
                )
                self.days[today]["complete"] = False
                self.days[today]["started"] = not self.gap and delta == 0
            self.days[today]["volume"] = str(decimal(self.days[today]["volume"]) + delta)
        self.source_last = str(current)
        self.source_at = now.isoformat()
        cutoff = (now.date() - timedelta(days=15)).isoformat()
        self.days = {key: item for key, item in self.days.items() if key >= cutoff}

    def calibrate(
        self,
        reading: Any,
        raw: Any,
        now: datetime,
        *,
        physical: bool,
        accept_large: bool = False,
        replace_meter: bool = False,
    ) -> None:
        value = decimal(reading)
        if value > 99999:
            raise GasError("reading_out_of_range")
        if self.actual is not None and value < decimal(self.actual) and not replace_meter:
            raise GasError("below_last_physical")
        if self.value is not None and abs(value - decimal(self.value)) > 5 and not accept_large:
            raise GasError("confirm_large_correction")
        if raw is not None:
            decimal(raw)  # Validate before mutating the checkpoint.
            self.observe(raw, now)
        old = self.value
        self.value = str(value)
        if physical:
            self.actual, self.actual_at = str(value), now.isoformat()
            self.gap = raw is None
        if replace_meter:
            self.days.clear()
        self.record(
            "physical" if physical else "adjustment",
            now,
            value=str(value),
            previous=old,
            meter_replaced=replace_meter,
        )

    def average(self, today: date) -> tuple[Decimal | None, int]:
        # Completed LOCAL calendar days only; never an average of active hours.
        selected = [
            decimal(day["volume"])
            for key, day in self.days.items()
            if today - timedelta(days=14) <= date.fromisoformat(key) < today
            and day.get("complete")
            and decimal(day["volume"]) > 0
        ]
        return (sum(selected, Decimal(0)) / len(selected), len(selected)) if selected else (None, 0)

    def dump(self) -> dict:
        return asdict(self)


def forecast(
    estimate: Estimate,
    bills: list[Bill],
    window: MeterWindow | None,
    source_configured: bool,
    now: datetime,
    tariff: Tariff | None,
    caloric: dict | None = None,
) -> dict:
    """Separate register estimate, consumption, full-period forecast and amounts."""
    result: dict = {
        "reading": estimate.value,
        "origin": "sensor" if source_configured else "manual",
        "average": None,
        "average_days": 0,
        "usage": None,
        "projected_usage": None,
        "accrued_amount": None,
        "projected_amount": None,
        "period_start": None,
        "period_end": None,
        "period_end_estimated": True,
        "quality": "insufficient_data",
    }
    if not bills or window is None or window.planned is None:
        return result
    latest = max(bills, key=lambda b: b.month)
    start = latest.end + timedelta(days=1)
    end = date.fromisoformat(window.planned)
    if not start <= now.date() <= end:
        return result
    result.update(period_start=start.isoformat(), period_end=end.isoformat())
    duration = (end - start).days + 1
    elapsed = (
        Decimal(
            str((now - datetime.combine(start, datetime.min.time(), now.tzinfo)).total_seconds())
        )
        / 86400
    )
    remaining = max(Decimal(0), Decimal(duration) - elapsed)
    usage = projected = None
    if source_configured:
        avg, count = estimate.average(now.date())
        result.update(average=str(avg) if avg is not None else None, average_days=count)
        if estimate.value is not None and not estimate.gap:
            # Physical meter identity must agree with the latest bill.
            if latest.segments[-1].meter == window.meter:
                usage = decimal(estimate.value) - decimal(latest.segments[-1].current)
                if usage < 0:
                    usage = None
            if usage is not None and avg is not None:
                projected = usage + avg * remaining
    else:
        matching = next((b for b in bills if b.month == f"{end.year - 1}{end.month:02}"), None)
        if matching:
            daily = matching.usage / ((matching.end - matching.start).days + 1)
            usage, projected = daily * elapsed, daily * duration
            # Newer physical observations are better register anchors, but never
            # upgrade this model-only reading to a sensor/physical observation.
            anchor, anchor_at = (
                decimal(window.previous),
                datetime.combine(start, datetime.min.time(), now.tzinfo),
            )
            if estimate.actual is not None and estimate.actual_at:
                at = datetime.fromisoformat(estimate.actual_at)
                if start <= at.date() <= now.date():
                    anchor, anchor_at = decimal(estimate.actual), at
            result.update(
                reading=str(
                    anchor + daily * Decimal(str((now - anchor_at).total_seconds())) / 86400
                ),
                origin="historical",
            )
    if usage is not None:
        result["usage"] = str(usage)
    if projected is not None:
        result.update(projected_usage=str(projected), quality="estimated")
    if tariff and not latest.unsupported_adjustments and tariff.effective <= now.date().isoformat():
        segment = latest.segments[-1]
        heat_factor = segment.heat_factor
        if (
            caloric
            and caloric["start"] == start.isoformat()
            and caloric["end"] <= now.date().isoformat()
        ):
            heat_factor = caloric["factor"]
            result["heat_coverage_end"] = caloric["end"]
            result["heat_partial"] = caloric["partial"]
        for name, volume in [("accrued_amount", usage), ("projected_amount", projected)]:
            if volume is not None:
                result[name] = str(
                    tariff.estimate(volume, segment.coefficient, heat_factor, latest.base_charge)
                )
        result["heat_factor"] = heat_factor
        result["factors_source"] = (
            "current_heat_previous_volume_provisional"
            if result.get("heat_coverage_end")
            else "latest_bill_provisional"
        )
        result["tariff_effective"] = tariff.effective
    return result
