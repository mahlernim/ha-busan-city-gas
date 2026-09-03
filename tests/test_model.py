from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from custom_components.busan_city_gas.model import (
    Bill,
    Estimate,
    GasError,
    MeterWindow,
    Segment,
    Tariff,
    decimal,
    forecast,
)

TZ = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 9, 2, 12, tzinfo=TZ)


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-1", "unknown", None, ""])
def test_invalid_numbers(value):
    with pytest.raises(GasError):
        decimal(value)


def test_counter_and_calibration_are_separate():
    e = Estimate()
    e.calibrate("35", "100", NOW, physical=True)
    e.observe("100.02", NOW + timedelta(seconds=1))
    assert decimal(e.value) == Decimal("35.02")
    e.calibrate("35.1", "100.02", NOW + timedelta(seconds=2), physical=False)
    assert e.actual == "35"
    assert e.days["2026-09-02"]["volume"] == "0.02"
    restored = Estimate(**e.dump())
    restored.observe("100.02", NOW + timedelta(seconds=3))
    assert decimal(restored.value) == decimal(e.value)


def test_reset_requires_calibration():
    e = Estimate()
    e.calibrate("35", "100", NOW, physical=True)
    e.observe("0.03", NOW + timedelta(seconds=1))
    assert decimal(e.value) == Decimal("35.03")
    assert e.gap
    e.calibrate("35.2", ".03", NOW + timedelta(seconds=2), physical=True)
    assert not e.gap


def test_missing_not_zero():
    e = Estimate()
    e.calibrate("35", "100", NOW, physical=True)
    e.observe("unavailable", NOW)
    assert e.gap and e.value == "35"
    e.observe("100", NOW)
    assert e.gap


def test_large_and_lower_changes():
    e = Estimate()
    e.calibrate("35", "10", NOW, physical=True)
    with pytest.raises(GasError, match="confirm_large"):
        e.calibrate("50", "10", NOW, physical=True)
    with pytest.raises(GasError, match="below_last"):
        e.calibrate("0", "10", NOW, physical=True, accept_large=True)
    e.calibrate("0", "10", NOW, physical=True, accept_large=True, replace_meter=True)
    assert e.actual == "0" and not e.days


def test_zero_and_incomplete_days_excluded():
    e = Estimate(
        days={
            "2026-08-18": {"volume": "999", "complete": True},  # outside 14 days
            "2026-08-19": {"volume": "2", "complete": True},
            "2026-08-20": {"volume": "0", "complete": True},
            "2026-08-21": {"volume": "100", "complete": False},
            "2026-09-01": {"volume": "4", "complete": True},
            "2026-09-02": {"volume": "200", "complete": True},  # today incomplete
        }
    )
    assert e.average(NOW.date()) == (Decimal(3), 2)


def test_all_zero_is_insufficient_not_division_by_zero():
    e = Estimate(days={"2026-09-01": {"volume": "0", "complete": True}})
    assert e.average(NOW.date()) == (None, 0)


def test_midnight_checkpoint():
    e = Estimate(
        value="35",
        source_last="0",
        source_at="2026-09-01T23:59:00+09:00",
        gap=False,
        days={"2026-09-01": {"volume": "2", "complete": False, "started": True}},
    )
    e.observe("0", datetime(2026, 9, 2, tzinfo=TZ))
    assert e.average(date(2026, 9, 2)) == (Decimal(2), 1)


def bill(month="202608"):
    return Bill(
        month,
        "14860",
        [Segment("2026-07-18", "2026-08-17", "14", "27", ".9846", "42.444", "meter")],
    )


def test_linear_forecast_and_gap_no_fallback():
    e = Estimate(
        value="35",
        actual="35",
        actual_at=NOW.isoformat(),
        gap=False,
        days={"2026-09-01": {"volume": "2", "complete": True}},
    )
    w = MeterWindow("2026-09-13", "2026-09-18", "27", "meter", "2026-09-17", True)
    result = forecast(
        e,
        [bill()],
        w,
        True,
        NOW,
        Tariff("2026-09-01", [{"up_to_mj": None, "rate": "23.2186"}], "900"),
    )
    assert result["usage"] == "8"
    assert result["projected_usage"] == "39.0"  # 15.5 days remaining
    assert result["average_days"] == 1
    e.gap = True
    result = forecast(e, [bill()], w, True, NOW, None)
    assert result["usage"] is None and result["projected_usage"] is None


def test_historical_day_normalization():
    old = Bill("202509", "0", [Segment("2025-08-18", "2025-09-17", "0", "31", "1", "42", "meter")])
    w = MeterWindow("2026-09-13", "2026-09-18", "27", "meter", "2026-09-17", True)
    result = forecast(Estimate(), [old, bill()], w, False, NOW, None)
    assert Decimal(result["projected_usage"]) == 31
    assert result["reading"] == "42.5"
    assert result["origin"] == "historical"


def test_heat_segment_truncation_june_known_values():
    segments = [
        Segment("2026-05-20", "2026-05-31", "2775", "2783", ".9846", "42.5350"),
        Segment("2026-06-01", "2026-06-15", "2783", "2793", ".9845", "42.3670"),
        Segment("2026-06-16", "2026-06-19", "0", "0", ".9845", "42.3420"),
    ]
    b = Bill("202606", "20190", segments)
    assert b.usage == Decimal(18)
    assert b.heat == Decimal("752.1427")


def test_tariff_bands_truncate_separately():
    tariff = Tariff(
        "2026-09-01",
        [
            {"up_to_mj": "516", "rate": "23.2186"},
            {"up_to_mj": None, "rate": "23.2186"},
        ],
        "900",
    )
    heat = Decimal("600")
    expected_subtotal = (
        Decimal(900)
        + int(Decimal(516) * Decimal("23.2186"))
        + int(Decimal(84) * Decimal("23.2186"))
    )
    expected = int((expected_subtotal + int(expected_subtotal / 10)) / 10) * 10
    assert tariff.estimate(heat, "1", "1", "900") == expected


def test_legacy_tariff_cache_loads_without_changing_busan_calculation():
    tariff = Tariff.load(
        {
            "effective": "2026-09-01",
            "first_rate": "23.2186",
            "second_rate": "23.2186",
            "threshold_mj": "516",
        }
    )
    assert tariff.bands[0]["up_to_mj"] == "516"
    assert tariff.estimate(Decimal("600"), "1", "1", "900") > 0
