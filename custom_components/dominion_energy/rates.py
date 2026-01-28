"""VA Residential Rate Schedule 1 data and calculation engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class Season(Enum):
    """Billing season."""

    SUMMER = "summer"  # Jun-Sep
    WINTER = "winter"  # Oct-May


@dataclass(frozen=True)
class TieredRate:
    """Rate with a kWh boundary (e.g., first 800 kWh vs. over 800 kWh)."""

    boundary_kwh: float
    rate_under: float  # $/kWh for usage <= boundary
    rate_over: float  # $/kWh for usage > boundary


@dataclass(frozen=True)
class SeasonalTieredRates:
    """Summer and winter tiered rates for a component."""

    summer: TieredRate
    winter: TieredRate


@dataclass(frozen=True)
class FlatRider:
    """A flat per-kWh rider/surcharge."""

    name: str
    rate: float  # $/kWh


@dataclass(frozen=True)
class ConsumptionTaxTier:
    """A consumption tax tier with kWh range."""

    lower_kwh: float  # inclusive
    upper_kwh: float  # exclusive (use float('inf') for unbounded)
    rate: float  # $/kWh


@dataclass(frozen=True)
class RateSchedule:
    """Complete rate schedule for a Dominion Energy tariff."""

    name: str
    effective_date: str
    customer_charge: float  # $/month flat charge
    distribution: SeasonalTieredRates
    generation: SeasonalTieredRates
    transmission_rate: float  # $/kWh flat
    riders: list[FlatRider] = field(default_factory=list)
    consumption_tax_tiers: list[ConsumptionTaxTier] = field(default_factory=list)


# Virginia Residential Schedule 1 — effective 2026-01-01
# Source: bill-calculator-worksheet-va.xlsx (last updated 2025-12-19)
VA_SCHEDULE_1 = RateSchedule(
    name="Schedule 1 - VA Residential",
    effective_date="2026-01-01",
    customer_charge=7.58,
    distribution=SeasonalTieredRates(
        summer=TieredRate(boundary_kwh=800, rate_under=0.03569, rate_over=0.023596),
        winter=TieredRate(boundary_kwh=800, rate_under=0.03569, rate_over=0.023596),
    ),
    generation=SeasonalTieredRates(
        summer=TieredRate(boundary_kwh=800, rate_under=0.031212, rate_over=0.046243),
        winter=TieredRate(boundary_kwh=800, rate_under=0.030064, rate_over=0.026965),
    ),
    transmission_rate=0.0097,
    riders=[
        FlatRider("C1A", 0.000231),
        FlatRider("C4A", 0.001336),
        FlatRider("DIST", 0.006241),
        FlatRider("RBB", 0.000531),
        FlatRider("E", 0.000625),
        FlatRider("GEN", 0.007564),
        FlatRider("SMR", 0.000287),
        FlatRider("SNA", 0.003475),
        FlatRider("CCR", 0.001765),
        FlatRider("CE", 0.003668),
        FlatRider("OSW", 0.011229),
        FlatRider("RPS", 0.007676),
        FlatRider("T1", 0.011789),
        FlatRider("Fuel/A", 0.02968),
        FlatRider("DFCC", 0.002906),
        FlatRider("Sales&Use", 0.000921),
    ],
    consumption_tax_tiers=[
        ConsumptionTaxTier(lower_kwh=0, upper_kwh=2500, rate=0.001565),
        ConsumptionTaxTier(lower_kwh=2500, upper_kwh=50000, rate=0.001055),
        ConsumptionTaxTier(lower_kwh=50000, upper_kwh=float("inf"), rate=0.000845),
    ],
)


def get_season(month: int) -> Season:
    """Determine billing season from month number (1-12)."""
    if 6 <= month <= 9:
        return Season.SUMMER
    return Season.WINTER


def calculate_tiered_cost(
    interval_kwh: float,
    cumulative_before: float,
    tiered_rate: TieredRate,
) -> float:
    """Calculate cost for a single interval using tiered pricing.

    Handles the case where cumulative usage straddles the tier boundary
    within this interval.

    Args:
        interval_kwh: kWh consumed in this interval.
        cumulative_before: Total kWh consumed before this interval in the billing period.
        tiered_rate: The tiered rate to apply.

    Returns:
        Cost in dollars for this interval.
    """
    boundary = tiered_rate.boundary_kwh
    cumulative_after = cumulative_before + interval_kwh

    if cumulative_after <= boundary:
        # All usage in lower tier
        return interval_kwh * tiered_rate.rate_under
    if cumulative_before >= boundary:
        # All usage in upper tier
        return interval_kwh * tiered_rate.rate_over

    # Straddles the boundary
    kwh_under = boundary - cumulative_before
    kwh_over = interval_kwh - kwh_under
    return kwh_under * tiered_rate.rate_under + kwh_over * tiered_rate.rate_over


def calculate_consumption_tax(
    interval_kwh: float,
    cumulative_before: float,
    tax_tiers: list[ConsumptionTaxTier],
) -> float:
    """Calculate consumption tax for an interval across tiered tax brackets.

    Args:
        interval_kwh: kWh consumed in this interval.
        cumulative_before: Total kWh consumed before this interval in the billing period.
        tax_tiers: List of consumption tax tiers (must be sorted by lower_kwh).

    Returns:
        Tax in dollars for this interval.
    """
    tax = 0.0
    remaining = interval_kwh
    position = cumulative_before

    for tier in tax_tiers:
        if remaining <= 0:
            break
        if position >= tier.upper_kwh:
            # Already past this tier
            continue
        if position < tier.lower_kwh:
            # Shouldn't happen with contiguous tiers, but handle gracefully
            position = tier.lower_kwh

        # How much of the remaining interval falls in this tier
        room_in_tier = tier.upper_kwh - position
        kwh_in_tier = min(remaining, room_in_tier)
        tax += kwh_in_tier * tier.rate
        remaining -= kwh_in_tier
        position += kwh_in_tier

    return tax


def calculate_schedule1_interval_cost(
    interval_kwh: float,
    interval_dt: datetime,
    cumulative_before: float,
    schedule: RateSchedule,
    billing_period_days: int = 30,
) -> float:
    """Calculate full Schedule 1 cost for a single 30-minute interval.

    Args:
        interval_kwh: kWh consumed in this interval.
        interval_dt: Timestamp of the interval (used for season determination).
        cumulative_before: Total kWh consumed before this interval in the billing period.
        schedule: The rate schedule to use.
        billing_period_days: Length of billing period in days (for prorating customer charge).

    Returns:
        Total cost in dollars for this interval.
    """
    if interval_kwh <= 0:
        return 0.0

    season = get_season(interval_dt.month)

    # Distribution (tiered)
    dist_rate = (
        schedule.distribution.summer
        if season == Season.SUMMER
        else schedule.distribution.winter
    )
    dist_cost = calculate_tiered_cost(interval_kwh, cumulative_before, dist_rate)

    # Generation (tiered)
    gen_rate = (
        schedule.generation.summer
        if season == Season.SUMMER
        else schedule.generation.winter
    )
    gen_cost = calculate_tiered_cost(interval_kwh, cumulative_before, gen_rate)

    # Transmission (flat)
    trans_cost = interval_kwh * schedule.transmission_rate

    # Riders (flat per kWh)
    rider_cost = sum(rider.rate * interval_kwh for rider in schedule.riders)

    # Consumption tax (tiered)
    tax_cost = calculate_consumption_tax(
        interval_kwh, cumulative_before, schedule.consumption_tax_tiers
    )

    # Prorated customer charge: $7.58/month spread across all intervals
    # 48 intervals/day * billing_period_days
    intervals_in_period = 48 * billing_period_days
    customer_charge_per_interval = schedule.customer_charge / intervals_in_period

    return (
        dist_cost
        + gen_cost
        + trans_cost
        + rider_cost
        + tax_cost
        + customer_charge_per_interval
    )
