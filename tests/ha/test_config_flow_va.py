"""Config flow tests for Virginia/North Carolina reauth."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from dompower import GigyaAuthenticator, LoginResult, TokenPair
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dominion_energy.const import (
    CONF_ACCESS_TOKEN,
    CONF_ACCOUNT_NUMBER,
    CONF_METER_NUMBER,
    CONF_PASSWORD,
    CONF_REFRESH_TOKEN,
    CONF_USERNAME,
    DOMAIN,
)

CONFIG_FLOW = "custom_components.dominion_energy.config_flow"


def _va_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id="123",
        title="Dominion Energy (123)",
        data={
            CONF_USERNAME: "me@example.com",
            CONF_PASSWORD: "pw",
            CONF_ACCESS_TOKEN: "old-access",
            CONF_REFRESH_TOKEN: "old-refresh",
            CONF_ACCOUNT_NUMBER: "123",
            CONF_METER_NUMBER: "456",
        },
    )


async def _reauth(hass: HomeAssistant, entry: MockConfigEntry, account_numbers):
    """Run a reauth that logs in without TFA and finds these accounts."""
    client = MagicMock()
    client.async_get_customer_info = AsyncMock(
        return_value=SimpleNamespace(
            accounts=[SimpleNamespace(account_number=n) for n in account_numbers]
        )
    )
    with (
        patch.object(GigyaAuthenticator, "async_init_session", AsyncMock()),
        patch.object(
            GigyaAuthenticator,
            "async_submit_credentials",
            AsyncMock(return_value=LoginResult(success=True, tfa_required=False)),
        ),
        patch.object(
            GigyaAuthenticator,
            "_async_complete_login",
            AsyncMock(return_value=TokenPair("new-access", "new-refresh")),
        ),
        patch.object(GigyaAuthenticator, "export_cookies", return_value={}),
        patch(f"{CONFIG_FLOW}.DompowerClient", return_value=client),
        patch("custom_components.dominion_energy.async_setup_entry", return_value=True),
    ):
        return await entry.start_reauth_flow(hass)


@pytest.mark.parametrize(
    ("account_numbers", "reason", "access_token"),
    [
        (["999", "123"], "reauth_successful", "new-access"),
        (["999"], "wrong_account", "old-access"),
    ],
)
async def test_va_reauth_checks_account(
    hass: HomeAssistant, account_numbers, reason, access_token
) -> None:
    """VA/NC reauth updates the entry only for a login that has its account."""
    entry = _va_entry()
    entry.add_to_hass(hass)

    result = await _reauth(hass, entry, account_numbers)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == reason
    assert entry.data[CONF_ACCESS_TOKEN] == access_token
