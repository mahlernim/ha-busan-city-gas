"""Synthetic correctness cases, not evidence of household forecast accuracy."""

from copy import deepcopy
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from custom_components.busan_city_gas.model import Bill, Estimate, MeterWindow, Segment, forecast
from custom_components.busan_city_gas.sensorless import (
    SensorlessModel,
    integrate,
    rate_at,
    recent_rate,
)

TZ = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 9, 1, tzinfo=TZ)


def history(daily="1"):
    return [
        Bill(
            "202509",
            "0",
            [Segment("2025-08-01", "2025-10-31", "0", str(Decimal(daily) * 92), "1", "42", "old")],
        )
    ]


def trained(daily="1", actual_rate="3"):
    model = SensorlessModel()
    model.state["initialized"] = True
    model.observe_physical("10", NOW - timedelta(days=14))
    model.observe_physical(str(10 + Decimal(actual_rate) * 14), NOW)
    model.sync(history(daily), None, Estimate(), NOW)
    return model


def test_blend_and_decay():
    m = trained()
    assert m.snapshot(NOW)["recent_weight"] == "0.5"
    assert Decimal(m.snapshot(NOW)["estimated_daily_usage"]) == 2
    assert rate_at(m.state["policy"], NOW + timedelta(days=21))[2] == Decimal("0.25")
    assert rate_at(m.state["policy"], NOW + timedelta(days=28))[2] == 0
    # 14 days at 2, then 14 days linearly dropping from 2 to 1.
    assert m.future_usage(NOW, NOW + timedelta(days=28)) == 49
    assert m.reading(NOW + timedelta(days=28)) == 101


def test_no_anchor_does_not_invent_absolute_reading():
    m = SensorlessModel()
    m.sync(history(), None, Estimate(), NOW)
    assert m.snapshot(NOW)["estimation_status"] == "anchor_required"
    assert m.reading(NOW) is None


def test_v030_migration_is_idempotent_and_does_not_replay_history():
    e = Estimate(actual="42", actual_at=NOW.isoformat())
    e.history = [{"kind": "physical", "value": "1", "at": (NOW - timedelta(days=14)).isoformat()}]
    before = deepcopy(e.dump())
    m = SensorlessModel()
    m.sync(history(), None, e, NOW)
    assert len(m.state["observations"]) == 1
    assert m.state["policy"]["recent"] is None
    saved = deepcopy(m.state)
    m = SensorlessModel(saved)
    m.sync(history(), None, e, NOW)
    assert m.state == saved
    assert e.dump() == before


def test_observation_retention_and_meter_replacement():
    m = SensorlessModel()
    m.state["initialized"] = True
    for days in (100, 89, 14, 0):
        m.observe_physical(str(200 - days), NOW - timedelta(days=days))
    m.sync(history(), None, Estimate(), NOW)
    assert len(m.state["observations"]) == 3
    m.observe_physical("2", NOW + timedelta(days=1), replace=True)
    m.sync(history(), None, Estimate(), NOW + timedelta(days=1))
    assert len(m.state["observations"]) == 1
    assert m.state["policy"]["recent"] is None


def test_official_anchor_requires_current_meter_and_date():
    bill = Bill("202608", "0", [Segment("2026-08-01", "2026-08-31", "0", "20", "1", "42", "A")])
    m = SensorlessModel()
    w = MeterWindow("2026-09-13", "2026-09-18", "999", "B")
    m.sync(history() + [bill], w, Estimate(), NOW)
    assert m.reading(NOW) is None
    w.meter = "A"
    m.sync(history() + [bill], w, Estimate(), NOW)
    assert m.reading(NOW) == 20
    assert m.state["observations"] == []
    assert m.reading(NOW + timedelta(days=1)) == 21


def test_single_anchor_no_learning_and_no_planned_date_required():
    e = Estimate(value="100", actual="100", actual_at=NOW.isoformat())
    result = forecast(e, history(), None, False, NOW + timedelta(days=2), None)
    assert Decimal(result["reading"]) == 102
    assert result["estimation_status"] == "learning_pending"
    assert result["origin"] == "historical"


def test_true_zero_is_valid_and_missing_is_not_zero():
    m = trained("0", "0")
    assert m.reading(NOW + timedelta(days=10)) == 10
    assert m.snapshot(NOW)["estimation_status"] == "ready"
    p = {"periods": [], "recent": "2", "last_actual_at": NOW.isoformat()}
    assert integrate(p, NOW, NOW + timedelta(days=14)) == 28
    assert integrate(p, NOW, NOW + timedelta(days=15)) is None


def test_short_duplicate_and_adjustments_not_learning():
    m = SensorlessModel()
    m.state["initialized"] = True
    m.observe_physical("10", NOW)
    m.observe_physical("12", NOW + timedelta(hours=1))
    assert len(m.state["observations"]) == 1
    m.observe_physical("100", NOW + timedelta(days=6))
    assert recent_rate(m.state["observations"])[0] is None
    before = deepcopy(m.state["observations"])
    m.observe_physical("101", NOW + timedelta(days=8), physical=False)
    assert m.state["observations"] == before


def test_oldest_observation_inside_28_day_window():
    obs = [
        {"at": (NOW - timedelta(days=d)).isoformat(), "value": str(50 - d)} for d in (29, 21, 7, 0)
    ]
    assert recent_rate(obs)[:2] == ("1", "21.0")


def test_policy_refresh_and_restart_preserve_value():
    m = trained()
    later = NOW + timedelta(days=3)
    value = m.reading(later)
    m.sync(history("4"), None, Estimate(), later)
    assert m.reading(later) == value
    restored = SensorlessModel(m.state)
    restored.sync(history("4"), None, Estimate(), later)
    assert restored.state == m.state
    assert restored.reading(later + timedelta(days=1)) == value + Decimal("3.5")


def test_leap_day_mapping_and_midnight_boundary():
    p = {"periods": [{"start": "2023-02-28", "end": "2023-02-28", "daily": "2"}]}
    leap = datetime(2024, 2, 29, tzinfo=TZ)
    assert integrate(p, leap, leap + timedelta(days=1)) == 2
    assert integrate(p, leap, leap + timedelta(days=2)) is None


def test_meter_change_never_reuses_old_physical():
    m = trained()
    w = MeterWindow("2026-09-13", "2026-09-18", "0", "A")
    e = Estimate(actual="52", actual_at=NOW.isoformat())
    m.sync(history(), w, e, NOW)
    w.meter = "B"
    m.sync(history(), w, e, NOW + timedelta(hours=1))
    assert m.state["observations"] == []
    assert m.reading(NOW + timedelta(hours=1)) is None


def test_latest_physical_wins_and_usage_uses_same_anchor():
    current = Bill("202608", "0", [Segment("2026-07-18", "2026-08-17", "0", "20", "1", "42", "A")])
    w = MeterWindow("2026-09-13", "2026-09-18", "999", "A", "2026-09-17")
    e = Estimate(actual="100", actual_at=NOW.isoformat())
    result = forecast(e, history() + [current], w, False, NOW + timedelta(days=1), None)
    assert Decimal(result["reading"]) == 101
    assert Decimal(result["usage"]) == 81
    assert Decimal(result["projected_usage"]) == 97


@pytest.mark.parametrize("days", [1, 7, 14, 21, 28])
def test_split_integration_matches_whole(days):
    m = trained()
    mid = NOW + timedelta(days=days / 2)
    end = NOW + timedelta(days=days)
    assert abs(
        m.future_usage(NOW, end) - m.future_usage(NOW, mid) - m.future_usage(mid, end)
    ) < Decimal("1e-20")
