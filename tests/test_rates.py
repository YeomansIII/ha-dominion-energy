"""Tests for VA Schedule 1 rate calculations."""

import sys
from datetime import datetime
from pathlib import Path

import pytest

# Add the custom_components/dominion_energy directory to sys.path so we can
# import rates.py directly without pulling in homeassistant via __init__.py
_pkg_dir = str(
    Path(__file__).resolve().parent.parent / "custom_components" / "dominion_energy"
)
if _pkg_dir not in sys.path:
    sys.path.insert(0, _pkg_dir)

from rates import (  # noqa: E402
    VA_SCHEDULE_1,
    Season,
    TieredRate,
    ConsumptionTaxTier,
    calculate_consumption_tax,
    calculate_schedule1_interval_cost,
    calculate_tiered_cost,
    get_season,
)


class TestGetSeason:
    """Tests for season determination."""

    def test_summer_months(self):
        for month in (6, 7, 8, 9):
            assert get_season(month) == Season.SUMMER

    def test_winter_months(self):
        for month in (1, 2, 3, 4, 5, 10, 11, 12):
            assert get_season(month) == Season.WINTER


class TestCalculateTieredCost:
    """Tests for tiered cost calculation."""

    rate = TieredRate(boundary_kwh=800, rate_under=0.04, rate_over=0.06)

    def test_all_under_boundary(self):
        # 0.5 kWh interval, cumulative 100 -> all under 800
        cost = calculate_tiered_cost(0.5, 100.0, self.rate)
        assert cost == pytest.approx(0.5 * 0.04)

    def test_all_over_boundary(self):
        # 0.5 kWh interval, cumulative already 900 -> all over 800
        cost = calculate_tiered_cost(0.5, 900.0, self.rate)
        assert cost == pytest.approx(0.5 * 0.06)

    def test_straddles_boundary(self):
        # 1.0 kWh interval, cumulative 799.5 -> 0.5 under + 0.5 over
        cost = calculate_tiered_cost(1.0, 799.5, self.rate)
        expected = 0.5 * 0.04 + 0.5 * 0.06
        assert cost == pytest.approx(expected)

    def test_exactly_at_boundary(self):
        # Cumulative exactly at boundary -> all over
        cost = calculate_tiered_cost(0.5, 800.0, self.rate)
        assert cost == pytest.approx(0.5 * 0.06)

    def test_interval_reaches_exactly_boundary(self):
        # 0.5 kWh from cumulative 799.5 -> ends exactly at 800, all under
        cost = calculate_tiered_cost(0.5, 799.5, self.rate)
        assert cost == pytest.approx(0.5 * 0.04)

    def test_zero_interval(self):
        cost = calculate_tiered_cost(0.0, 500.0, self.rate)
        assert cost == pytest.approx(0.0)


class TestCalculateConsumptionTax:
    """Tests for consumption tax calculation."""

    tiers = [
        ConsumptionTaxTier(lower_kwh=0, upper_kwh=2500, rate=0.001565),
        ConsumptionTaxTier(lower_kwh=2500, upper_kwh=50000, rate=0.001055),
        ConsumptionTaxTier(lower_kwh=50000, upper_kwh=float("inf"), rate=0.000845),
    ]

    def test_all_in_first_tier(self):
        tax = calculate_consumption_tax(1.0, 100.0, self.tiers)
        assert tax == pytest.approx(1.0 * 0.001565)

    def test_all_in_second_tier(self):
        tax = calculate_consumption_tax(1.0, 3000.0, self.tiers)
        assert tax == pytest.approx(1.0 * 0.001055)

    def test_straddles_first_second_tier(self):
        # Cumulative 2499, interval 2.0 -> 1 kWh in first + 1 kWh in second
        tax = calculate_consumption_tax(2.0, 2499.0, self.tiers)
        expected = 1.0 * 0.001565 + 1.0 * 0.001055
        assert tax == pytest.approx(expected)

    def test_zero_interval(self):
        tax = calculate_consumption_tax(0.0, 500.0, self.tiers)
        assert tax == pytest.approx(0.0)


class TestCalculateSchedule1IntervalCost:
    """Tests for full Schedule 1 interval cost calculation."""

    def test_zero_consumption(self):
        dt = datetime(2026, 7, 15, 12, 0)
        cost = calculate_schedule1_interval_cost(0.0, dt, 0.0, VA_SCHEDULE_1)
        assert cost == 0.0

    def test_single_interval_summer(self):
        # A single 30-min interval: 0.5 kWh, summer, no prior cumulative
        dt = datetime(2026, 7, 15, 12, 0)
        cost = calculate_schedule1_interval_cost(
            0.5, dt, 0.0, VA_SCHEDULE_1, billing_period_days=30
        )
        # Should be > 0 and include all components
        assert cost > 0

    def test_full_month_summer_1000kwh(self):
        """Verify a 1000 kWh summer month matches manual worksheet calculation.

        1000 kWh over 30 days = ~0.694 kWh per 30-min interval (48 intervals/day).
        Expected total: $176.2584 (calculated from worksheet rates).
        """
        kwh_per_interval = 1000.0 / (48 * 30)  # ~0.6944
        total_cost = 0.0
        cumulative = 0.0
        billing_days = 30

        for day in range(30):
            for half_hour in range(48):
                hour = half_hour // 2
                minute = (half_hour % 2) * 30
                dt = datetime(2026, 7, 1 + day, hour, minute)
                cost = calculate_schedule1_interval_cost(
                    kwh_per_interval,
                    dt,
                    cumulative,
                    VA_SCHEDULE_1,
                    billing_period_days=billing_days,
                )
                total_cost += cost
                cumulative += kwh_per_interval

        # Manual calculation: $176.2584
        assert total_cost == pytest.approx(176.2584, rel=1e-3)

    def test_full_month_winter_1000kwh(self):
        """Verify a 1000 kWh winter month.

        Distribution is same as summer. Generation differs:
        800 * 0.030064 + 200 * 0.026965 = 24.0512 + 5.393 = 29.4442
        vs summer generation = 34.2182
        Difference = -4.774
        Expected total: 176.2584 - 4.774 = 171.4844
        """
        kwh_per_interval = 1000.0 / (48 * 30)
        total_cost = 0.0
        cumulative = 0.0

        for day in range(30):
            for half_hour in range(48):
                hour = half_hour // 2
                minute = (half_hour % 2) * 30
                dt = datetime(2026, 1, 1 + day, hour, minute)
                cost = calculate_schedule1_interval_cost(
                    kwh_per_interval,
                    dt,
                    cumulative,
                    VA_SCHEDULE_1,
                    billing_period_days=30,
                )
                total_cost += cost
                cumulative += kwh_per_interval

        # Winter generation: 800 * 0.030064 + 200 * 0.026965 = 29.4442
        # Summer generation was 34.2182, diff = -4.774
        # Expected: 176.2584 - 4.774 = 171.4844
        assert total_cost == pytest.approx(171.4844, rel=1e-3)

    def test_low_usage_all_under_boundary(self):
        """500 kWh month should use only lower-tier rates."""
        kwh_per_interval = 500.0 / (48 * 30)
        total_cost = 0.0
        cumulative = 0.0

        for day in range(30):
            for half_hour in range(48):
                hour = half_hour // 2
                minute = (half_hour % 2) * 30
                dt = datetime(2026, 7, 1 + day, hour, minute)
                cost = calculate_schedule1_interval_cost(
                    kwh_per_interval,
                    dt,
                    cumulative,
                    VA_SCHEDULE_1,
                    billing_period_days=30,
                )
                total_cost += cost
                cumulative += kwh_per_interval

        # Manual: dist=500*0.03569=17.845, gen=500*0.031212=15.606,
        # trans=500*0.0097=4.85, riders=500*0.089924=44.962,
        # tax=500*0.001565=0.7825, cc=7.58
        # Total = 91.6255
        expected = 17.845 + 15.606 + 4.85 + 44.962 + 0.7825 + 7.58
        assert total_cost == pytest.approx(expected, rel=1e-3)

    def test_season_boundary_month_june(self):
        """June should use summer rates."""
        dt = datetime(2026, 6, 15, 12, 0)
        cost = calculate_schedule1_interval_cost(
            1.0, dt, 0.0, VA_SCHEDULE_1, billing_period_days=30
        )
        # Generation rate under for summer is 0.031212
        # vs winter 0.030064 — summer should yield slightly higher gen cost
        dt_winter = datetime(2026, 5, 15, 12, 0)
        cost_winter = calculate_schedule1_interval_cost(
            1.0, dt_winter, 0.0, VA_SCHEDULE_1, billing_period_days=30
        )
        # Summer gen rate is higher than winter for under-800 tier
        assert cost > cost_winter
