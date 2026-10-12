"""Region helpers for the Dominion Energy integration.

This module does not import Home Assistant, so tests can import it directly.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from .const import CONF_REGION, REGION_SC, REGION_VA_NC

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def get_region(data: Mapping[str, Any]) -> str:
    """Return the region of a config entry.

    Entries made before SC support have no region. They are VA/NC.
    """
    return str(data.get(CONF_REGION, REGION_VA_NC))


def is_sc(data: Mapping[str, Any]) -> bool:
    """Return True if the config entry is a South Carolina account."""
    return get_region(data) == REGION_SC


def sc_account_key(user_id: str) -> str:
    """Return the stable account key for an SC account.

    The key is used as the config entry unique ID, in statistic IDs, and as
    the device identifier. Statistic IDs allow only lowercase letters, digits,
    and underscores after the domain.
    """
    slug = _NON_ALNUM.sub("_", user_id.lower()).strip("_")
    return f"sc_{slug}"


def sc_derived_rate(
    cost_to_date: float,
    kwh_to_date: float,
    *,
    has_gas: bool,
) -> float | None:
    """Return an effective $/kWh rate from the SC bill forecast.

    The SC cost to date includes gas. For accounts with gas, the rate would
    be too high, so return None and let the caller use the fixed rate.
    """
    if has_gas or kwh_to_date <= 0 or cost_to_date <= 0:
        return None
    return cost_to_date / kwh_to_date
