"""Config flow tests for the region step and South Carolina accounts."""

from unittest.mock import AsyncMock, MagicMock, patch

from dompower import (
    ApiError,
    CannotConnectError,
    InvalidCredentialsError,
    LoginResult,
    MeterType,
    SCAccount,
    SCAuthenticator,
    SCSession,
    TFAProvider,
    TFATarget,
)
from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dominion_energy.config_flow import DominionEnergyConfigFlow
from custom_components.dominion_energy.const import (
    CONF_ACCOUNT_NUMBER,
    CONF_COST_MODE,
    CONF_METER_TYPES,
    CONF_PASSWORD,
    CONF_REGION,
    CONF_SERVICE_ADDRESS,
    CONF_TFA_TOKEN,
    CONF_USERNAME,
    COST_MODE_SCHEDULE_1,
    DOMAIN,
    REGION_SC,
    REGION_VA_NC,
)


USER_ID = "ABCD-1234"
ACCOUNT_KEY = "sc_abcd_1234"
ADDRESS = "1 MAIN ST (*-****-****0-4464)"
SC_SESSION = SCSession(
    access_token="bearer",
    account=SCAccount(
        user_id=USER_ID,
        service_address=ADDRESS,
        meter_types=[MeterType.ELECTRIC, MeterType.GAS],
    ),
)


def _mock_sc_auth(*, tfa_required: bool) -> MagicMock:
    """Return a mock SCAuthenticator that passes isinstance checks."""
    auth = MagicMock(spec=SCAuthenticator)
    auth.async_submit_credentials = AsyncMock(
        return_value=LoginResult(success=not tfa_required, tfa_required=tfa_required)
    )
    auth.async_get_tfa_options = AsyncMock(
        return_value=[
            TFATarget(
                id="me@example.com",
                obfuscated="me@example.com",
                provider=TFAProvider.EMAIL,
            )
        ]
    )
    auth.async_send_tfa_code = AsyncMock()
    auth.async_verify_tfa_code = AsyncMock(return_value="new-token")
    auth.async_complete_login = AsyncMock(return_value=SC_SESSION)
    auth.tfa_token = "new-token"
    return auth


def _patch_auth(auth: MagicMock):
    return patch.object(
        DominionEnergyConfigFlow, "_new_sc_authenticator", return_value=auth
    )


def _patch_setup():
    return patch(
        "custom_components.dominion_energy.async_setup_entry", return_value=True
    )


async def _start_sc_flow(hass: HomeAssistant) -> dict:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_REGION: REGION_SC}
    )
    assert result["step_id"] == "sc_credentials"
    return result


async def test_region_step_routes_va_nc(hass: HomeAssistant) -> None:
    """VA/NC goes to the Gigya credentials step."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_REGION: REGION_VA_NC}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "credentials"


async def test_sc_flow_with_tfa(hass: HomeAssistant) -> None:
    """A new SC login does TFA and creates the entry."""
    auth = _mock_sc_auth(tfa_required=True)
    result = await _start_sc_flow(hass)

    with _patch_auth(auth), _patch_setup():
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_USERNAME: "me", CONF_PASSWORD: "pw"}
        )
        assert result["step_id"] == "tfa_select"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"tfa_target": "me@example.com"}
        )
        assert result["step_id"] == "tfa_code"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"code": "123456"}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == f"Dominion Energy SC ({ADDRESS})"
    assert result["result"].unique_id == ACCOUNT_KEY
    assert result["data"] == {
        CONF_REGION: REGION_SC,
        CONF_USERNAME: "me",
        CONF_PASSWORD: "pw",
        CONF_TFA_TOKEN: "new-token",
        CONF_ACCOUNT_NUMBER: ACCOUNT_KEY,
        CONF_SERVICE_ADDRESS: ADDRESS,
        CONF_METER_TYPES: ["electric", "gas"],
    }
    auth.async_submit_credentials.assert_awaited_once_with("me", "pw", None)
    auth.async_verify_tfa_code.assert_awaited_once_with("123456")


async def test_sc_invalid_credentials(hass: HomeAssistant) -> None:
    """A wrong password shows an error on the SC credentials step."""
    auth = _mock_sc_auth(tfa_required=True)
    auth.async_submit_credentials.side_effect = InvalidCredentialsError("bad")
    result = await _start_sc_flow(hass)

    with _patch_auth(auth):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_USERNAME: "me", CONF_PASSWORD: "bad"}
        )

    assert result["step_id"] == "sc_credentials"
    assert result["errors"] == {"base": "invalid_credentials"}


async def test_sc_multiple_accounts(hass: HomeAssistant) -> None:
    """A login with many accounts shows the multiple_accounts error."""
    auth = _mock_sc_auth(tfa_required=True)
    auth.async_complete_login.side_effect = ApiError(
        "This SC login has more than one account."
    )
    result = await _start_sc_flow(hass)

    with _patch_auth(auth):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_USERNAME: "me", CONF_PASSWORD: "pw"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"tfa_target": "me@example.com"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"code": "123456"}
        )

    assert result["step_id"] == "tfa_code"
    assert result["errors"] == {"base": "multiple_accounts"}


def _sc_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=ACCOUNT_KEY,
        title=f"Dominion Energy SC ({ADDRESS})",
        data={
            CONF_REGION: REGION_SC,
            CONF_USERNAME: "me",
            CONF_PASSWORD: "pw",
            CONF_TFA_TOKEN: "saved-token",
            CONF_ACCOUNT_NUMBER: ACCOUNT_KEY,
            CONF_SERVICE_ADDRESS: ADDRESS,
            CONF_METER_TYPES: ["electric"],
        },
    )


async def test_sc_reauth_with_saved_token(hass: HomeAssistant) -> None:
    """Reauth uses the stored password and TFA token without user input."""
    entry = _sc_entry()
    entry.add_to_hass(hass)
    auth = _mock_sc_auth(tfa_required=False)
    auth.tfa_token = "saved-token"

    with _patch_auth(auth), _patch_setup():
        result = await entry.start_reauth_flow(hass)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    auth.async_submit_credentials.assert_awaited_once_with("me", "pw", "saved-token")
    assert entry.data[CONF_TFA_TOKEN] == "saved-token"
    assert entry.data[CONF_METER_TYPES] == ["electric", "gas"]


async def test_sc_reauth_needs_new_tfa(hass: HomeAssistant) -> None:
    """If the saved token is rejected, reauth does TFA and saves a new token."""
    entry = _sc_entry()
    entry.add_to_hass(hass)
    auth = _mock_sc_auth(tfa_required=True)

    with _patch_auth(auth), _patch_setup():
        result = await entry.start_reauth_flow(hass)
        assert result["step_id"] == "reauth_tfa_select"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"tfa_target": "me@example.com"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"code": "123456"}
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_TFA_TOKEN] == "new-token"


async def test_sc_flow_already_configured(hass: HomeAssistant) -> None:
    """A login for an SC account that already has an entry aborts."""
    _sc_entry().add_to_hass(hass)
    auth = _mock_sc_auth(tfa_required=False)
    result = await _start_sc_flow(hass)

    with _patch_auth(auth), _patch_setup():
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_USERNAME: "me", CONF_PASSWORD: "pw"}
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_sc_reauth_tfa_options_error_shows_form(hass: HomeAssistant) -> None:
    """A TFA options error shows the reauth form and does not log in again."""
    entry = _sc_entry()
    entry.add_to_hass(hass)
    auth = _mock_sc_auth(tfa_required=True)
    auth.async_get_tfa_options.side_effect = CannotConnectError("portal down")

    with _patch_auth(auth), _patch_setup():
        result = await entry.start_reauth_flow(hass)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"
    assert result["errors"] == {"base": "cannot_connect"}
    auth.async_submit_credentials.assert_awaited_once()


async def test_sc_reauth_wrong_account(hass: HomeAssistant) -> None:
    """Reauth with the login of a different SC account aborts."""
    entry = _sc_entry()
    entry.add_to_hass(hass)
    auth = _mock_sc_auth(tfa_required=False)
    auth.async_complete_login.return_value = SCSession(
        access_token="bearer",
        account=SCAccount(
            user_id="OTHER-9999",
            service_address="2 OTHER ST",
            meter_types=[MeterType.ELECTRIC],
        ),
    )

    with _patch_auth(auth), _patch_setup():
        result = await entry.start_reauth_flow(hass)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_account"
    assert entry.data[CONF_TFA_TOKEN] == "saved-token"


async def test_sc_flow_reuses_and_detaches_session(hass: HomeAssistant) -> None:
    """The flow uses one SC session for all attempts and detaches it at the end."""
    session = MagicMock()
    flow = DominionEnergyConfigFlow()
    flow.hass = hass

    with patch(
        "custom_components.dominion_energy.config_flow.async_create_clientsession",
        return_value=session,
    ) as create_session:
        flow._new_sc_authenticator()
        flow._new_sc_authenticator()
        flow.async_remove()

    create_session.assert_called_once()
    assert create_session.call_args.kwargs["auto_cleanup"] is False
    session.cookie_jar.clear.assert_called_once()
    session.detach.assert_called_once()


async def test_sc_options_hide_schedule_1(hass: HomeAssistant) -> None:
    """Schedule 1 (a Virginia tariff) is not offered for SC entries."""
    entry = _sc_entry()
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)

    schema = result["data_schema"].schema
    cost_mode_validator = next(v for k, v in schema.items() if str(k) == CONF_COST_MODE)
    assert COST_MODE_SCHEDULE_1 not in cost_mode_validator.container
