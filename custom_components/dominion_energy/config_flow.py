"""Config flow for Dominion Energy integration."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import aiohttp
import voluptuous as vol
from dompower import (
    AccountInfo,
    ApiError,
    CannotConnectError,
    DompowerClient,
    GigyaAuthenticator,
    GigyaError,
    InvalidAuthError,
    InvalidCredentialsError,
    LoginResult,
    MeterDevice,
    SCAuthenticator,
    SCSession,
    TFAExpiredError,
    TFATarget,
    TFAVerificationError,
    TokenExpiredError,
    TokenPair,
    create_sc_cookie_jar,
)
from homeassistant.config_entries import (
    SOURCE_RECONFIGURE,
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import (
    async_create_clientsession,
    async_get_clientsession,
)

from .const import (
    CONF_ACCESS_TOKEN,
    CONF_ACCOUNT_NUMBER,
    CONF_COOKIES,
    CONF_COST_MODE,
    CONF_FIXED_RATE,
    CONF_METER_NUMBER,
    CONF_METER_TYPES,
    CONF_OFF_PEAK_RATE,
    CONF_PASSWORD,
    CONF_PEAK_END_HOUR,
    CONF_PEAK_RATE,
    CONF_PEAK_START_HOUR,
    CONF_REFRESH_TOKEN,
    CONF_REGION,
    CONF_SERVICE_ADDRESS,
    CONF_TFA_TOKEN,
    CONF_USERNAME,
    COST_MODE_API,
    COST_MODE_FIXED,
    COST_MODE_SCHEDULE_1,
    COST_MODE_TOU,
    DEFAULT_FIXED_RATE,
    DEFAULT_OFF_PEAK_RATE,
    DEFAULT_PEAK_END_HOUR,
    DEFAULT_PEAK_RATE,
    DEFAULT_PEAK_START_HOUR,
    DOMAIN,
    REGION_SC,
    REGION_VA_NC,
)
from .rates import VA_SCHEDULE_1
from .region import get_region, is_sc, sc_account_key

_LOGGER = logging.getLogger(__name__)

# User step schema - service area
STEP_REGION_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_REGION, default=REGION_VA_NC): vol.In(
            {
                REGION_VA_NC: "Virginia or North Carolina",
                REGION_SC: "South Carolina",
            }
        ),
    }
)

# Credentials step schema - username and password
STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)

# TFA code step schema
STEP_TFA_CODE_SCHEMA = vol.Schema(
    {
        vol.Required("code"): str,
    }
)


class DominionEnergyConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Dominion Energy."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._region: str = REGION_VA_NC
        self._username: str | None = None
        self._password: str | None = None
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._cookies: dict | None = None
        self._authenticator: GigyaAuthenticator | SCAuthenticator | None = None
        self._tfa_targets: list[TFATarget] = []
        self._selected_tfa_target: TFATarget | None = None
        self._account_meter_options: dict[str, tuple[AccountInfo, MeterDevice]] = {}
        self._sc_session: aiohttp.ClientSession | None = None

    @callback
    def async_remove(self) -> None:
        """Detach the SC session when the flow ends."""
        if self._sc_session is not None:
            self._sc_session.detach()
            self._sc_session = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Get the options flow for this handler."""
        return DominionEnergyOptionsFlow(config_entry)

    @property
    def _credentials_step_id(self) -> str:
        """Return the credentials step for the current region."""
        return "sc_credentials" if self._region == REGION_SC else "credentials"

    def _show_credentials_form(self, errors: dict[str, str]) -> ConfigFlowResult:
        """Show the credentials form for the current region again."""
        return self.async_show_form(
            step_id=self._credentials_step_id,
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    def _new_sc_authenticator(self) -> SCAuthenticator:
        """Return an SC authenticator with an empty cookie jar.

        The SC portal uses session cookies, so the session must not be shared
        with other integrations. The flow keeps one session for all login
        attempts and detaches it in async_remove.
        """
        if self._sc_session is None:
            self._sc_session = async_create_clientsession(
                self.hass, auto_cleanup=False, cookie_jar=create_sc_cookie_jar()
            )
        else:
            self._sc_session.cookie_jar.clear()
        return SCAuthenticator(self._sc_session)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step - select the service area."""
        if user_input is not None:
            self._region = user_input[CONF_REGION]
            if self._region == REGION_SC:
                return await self.async_step_sc_credentials()
            return await self.async_step_credentials()

        return self.async_show_form(step_id="user", data_schema=STEP_REGION_SCHEMA)

    async def async_step_credentials(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect username and password for a VA/NC account."""
        errors: dict[str, str] = {}
        suggested_values: dict[str, Any] | None = None

        # Prefill form with existing data when reconfiguring
        if self.source == SOURCE_RECONFIGURE:
            reconfigure_entry = self._get_reconfigure_entry()
            suggested_values = {
                CONF_USERNAME: reconfigure_entry.data.get(CONF_USERNAME, ""),
                CONF_PASSWORD: reconfigure_entry.data.get(CONF_PASSWORD, ""),
            }

        if user_input is not None:
            self._username = user_input[CONF_USERNAME]
            self._password = user_input[CONF_PASSWORD]

            try:
                # Create authenticator and attempt login
                session = async_get_clientsession(self.hass)
                authenticator = GigyaAuthenticator(session)
                self._authenticator = authenticator

                # Initialize session and submit credentials
                await authenticator.async_init_session()
                assert self._username is not None
                assert self._password is not None
                result = await authenticator.async_submit_credentials(
                    self._username, self._password
                )

                if result.tfa_required:
                    # TFA is required - proceed to TFA selection
                    return await self.async_step_tfa_select()

                # No TFA required - complete authentication
                tokens = await authenticator._async_complete_login()
                self._access_token = tokens.access_token
                self._refresh_token = tokens.refresh_token
                self._cookies = authenticator.export_cookies()

                # Proceed to account discovery
                return await self.async_step_discover_accounts()

            except InvalidCredentialsError:
                errors["base"] = "invalid_credentials"
            except CannotConnectError:
                errors["base"] = "cannot_connect"
            except GigyaError as err:
                _LOGGER.error("Gigya authentication error: %s", err)
                errors["base"] = "unknown"
            except Exception:
                _LOGGER.exception("Unexpected exception during login")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="credentials",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_DATA_SCHEMA, suggested_values
            ),
            errors=errors,
        )

    async def async_step_sc_credentials(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect username and password for a South Carolina account."""
        errors: dict[str, str] = {}
        suggested_values: dict[str, Any] | None = None
        saved_tfa_token: str | None = None
        self._region = REGION_SC

        if self.source == SOURCE_RECONFIGURE:
            reconfigure_entry = self._get_reconfigure_entry()
            suggested_values = {
                CONF_USERNAME: reconfigure_entry.data.get(CONF_USERNAME, ""),
                CONF_PASSWORD: reconfigure_entry.data.get(CONF_PASSWORD, ""),
            }
            if user_input and user_input[CONF_USERNAME] == reconfigure_entry.data.get(
                CONF_USERNAME
            ):
                saved_tfa_token = reconfigure_entry.data.get(CONF_TFA_TOKEN)

        if user_input is not None:
            self._username = user_input[CONF_USERNAME]
            self._password = user_input[CONF_PASSWORD]

            try:
                authenticator = self._new_sc_authenticator()
                self._authenticator = authenticator
                assert self._username is not None
                assert self._password is not None
                result = await authenticator.async_submit_credentials(
                    self._username, self._password, saved_tfa_token
                )
                if result.tfa_required:
                    return await self.async_step_tfa_select()
                return await self._async_finish_sc_login()

            except InvalidCredentialsError:
                errors["base"] = "invalid_credentials"
            except CannotConnectError:
                errors["base"] = "cannot_connect"
            except ApiError as err:
                _LOGGER.error("SC API error during login: %s", err)
                errors["base"] = _sc_api_error_key(err)
            except (GigyaError, InvalidAuthError) as err:
                _LOGGER.error("SC authentication error: %s", err)
                errors["base"] = "unknown"
            except Exception:
                _LOGGER.exception("Unexpected exception during SC login")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="sc_credentials",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_DATA_SCHEMA, suggested_values
            ),
            errors=errors,
        )

    async def async_step_tfa_select(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle TFA target selection step."""
        errors: dict[str, str] = {}

        if self._authenticator is None:
            return self.async_abort(reason="unknown")

        # Get TFA options if we don't have them yet
        if not self._tfa_targets:
            try:
                self._tfa_targets = await self._authenticator.async_get_tfa_options()
            except (GigyaError, ApiError, CannotConnectError) as err:
                _LOGGER.error("Failed to get TFA options: %s", err)
                errors["base"] = "unknown"
                # Fall back to credentials step
                return self._show_credentials_form(errors)

        if not self._tfa_targets:
            _LOGGER.error("No TFA targets available")
            errors["base"] = "unknown"
            return self._show_credentials_form(errors)

        if user_input is not None:
            # User selected a TFA target
            selected_key = user_input["tfa_target"]

            # Find the selected target
            for target in self._tfa_targets:
                if target.id == selected_key:
                    self._selected_tfa_target = target
                    break

            if self._selected_tfa_target:
                try:
                    # Send TFA code to selected target
                    await self._authenticator.async_send_tfa_code(
                        self._selected_tfa_target
                    )
                    return await self.async_step_tfa_code()
                except (GigyaError, ApiError, CannotConnectError) as err:
                    _LOGGER.error("Failed to send TFA code: %s", err)
                    errors["base"] = "unknown"

        # Build TFA target options
        options: dict[str, str] = {}
        for target in self._tfa_targets:
            options[target.id] = target.obfuscated

        return self.async_show_form(
            step_id="tfa_select",
            data_schema=vol.Schema(
                {
                    vol.Required("tfa_target"): vol.In(options),
                }
            ),
            errors=errors,
        )

    async def async_step_tfa_code(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle TFA code entry step."""
        errors: dict[str, str] = {}

        if self._authenticator is None:
            return self.async_abort(reason="unknown")

        if user_input is not None:
            code = user_input["code"]

            try:
                if isinstance(self._authenticator, SCAuthenticator):
                    # Verify the code (this registers the device), then finish
                    await self._authenticator.async_verify_tfa_code(code)
                    return await self._async_finish_sc_login()

                # Verify TFA code and get tokens
                tokens = await self._authenticator.async_verify_tfa_code(code)
                self._access_token = tokens.access_token
                self._refresh_token = tokens.refresh_token
                self._cookies = self._authenticator.export_cookies()

                # Proceed to account discovery
                return await self.async_step_discover_accounts()

            except TFAVerificationError:
                errors["base"] = "tfa_failed"
            except TFAExpiredError:
                # TFA session expired - restart TFA flow
                errors["base"] = "tfa_expired"
                self._tfa_targets = []  # Clear to force re-fetch
                return await self.async_step_tfa_select()
            except CannotConnectError:
                errors["base"] = "cannot_connect"
            except ApiError as err:
                _LOGGER.error("API error after TFA: %s", err)
                errors["base"] = _sc_api_error_key(err)
            except (GigyaError, InvalidAuthError) as err:
                _LOGGER.error("TFA verification error: %s", err)
                errors["base"] = "unknown"

        # Show target info in description
        target_display = (
            self._selected_tfa_target.obfuscated
            if self._selected_tfa_target
            else "your device"
        )

        return self.async_show_form(
            step_id="tfa_code",
            data_schema=STEP_TFA_CODE_SCHEMA,
            errors=errors,
            description_placeholders={"target": target_display},
        )

    async def _async_finish_sc_login(self) -> ConfigFlowResult:
        """Complete the SC login and create or update the config entry."""
        assert isinstance(self._authenticator, SCAuthenticator)
        sc_session = await self._authenticator.async_complete_login()
        return await self._create_sc_entry(sc_session, self._authenticator.tfa_token)

    async def _create_sc_entry(
        self, sc_session: SCSession, tfa_token: str | None
    ) -> ConfigFlowResult:
        """Create or update the config entry for an SC account."""
        account = sc_session.account
        account_key = sc_account_key(account.user_id)
        await self.async_set_unique_id(account_key)

        new_data = {
            CONF_REGION: REGION_SC,
            CONF_USERNAME: self._username,
            CONF_PASSWORD: self._password,
            CONF_TFA_TOKEN: tfa_token,
            CONF_ACCOUNT_NUMBER: account_key,
            CONF_SERVICE_ADDRESS: account.service_address,
            CONF_METER_TYPES: [meter_type.value for meter_type in account.meter_types],
        }

        if self.source == SOURCE_RECONFIGURE:
            self._abort_if_unique_id_mismatch()
            return self.async_update_reload_and_abort(
                self._get_reconfigure_entry(),
                data=new_data,
            )

        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=f"Dominion Energy SC ({account.service_address})",
            data=new_data,
        )

    async def async_step_discover_accounts(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Discover accounts and meters from the API."""
        errors: dict[str, str] = {}

        try:
            session = async_get_clientsession(self.hass)
            client = DompowerClient(
                session,
                access_token=self._access_token,
                refresh_token=self._refresh_token,
            )

            # Fetch customer info with all accounts
            customer_info = await client.async_get_customer_info()

            # Build selection options: "account_number|meter_id" -> (AccountInfo, MeterDevice)
            self._account_meter_options = {}
            for account in customer_info.active_accounts:
                for meter in account.meters:
                    if meter.is_active and meter.has_ami:
                        key = f"{account.account_number}|{meter.device_id}"
                        self._account_meter_options[key] = (account, meter)

            if not self._account_meter_options:
                errors["base"] = "no_meters_found"
                return self._show_credentials_form(errors)

            # Auto-select if only one option
            if len(self._account_meter_options) == 1:
                key = next(iter(self._account_meter_options))
                return await self._create_entry_from_selection(key)

            # Multiple options - show selection UI
            return await self.async_step_select_meter()

        except (InvalidAuthError, TokenExpiredError):
            errors["base"] = "invalid_auth"
        except CannotConnectError:
            errors["base"] = "cannot_connect"
        except ApiError as err:
            _LOGGER.error("API error discovering accounts: %s", err)
            errors["base"] = "cannot_connect"
        except Exception:
            _LOGGER.exception("Unexpected exception during account discovery")
            errors["base"] = "unknown"

        # On error, return to credentials step
        return self._show_credentials_form(errors)

    async def async_step_select_meter(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle meter selection when multiple accounts/meters exist."""
        if user_input is not None:
            selected_key = user_input["meter_selection"]
            return await self._create_entry_from_selection(selected_key)

        # Build display options with service address context
        options: dict[str, str] = {}
        for key, (account, meter) in self._account_meter_options.items():
            # Format: "123 Main St - Meter: ...117800"
            address = str(account.service_address)
            meter_suffix = (
                meter.device_id[-8:] if len(meter.device_id) > 8 else meter.device_id
            )
            label = f"{address} - Meter: ...{meter_suffix}"
            options[key] = label

        return self.async_show_form(
            step_id="select_meter",
            data_schema=vol.Schema(
                {
                    vol.Required("meter_selection"): vol.In(options),
                }
            ),
        )

    async def _create_entry_from_selection(
        self, selection_key: str
    ) -> ConfigFlowResult:
        """Create or update config entry from the selected account/meter."""
        account, meter = self._account_meter_options[selection_key]

        # Set unique ID
        await self.async_set_unique_id(account.account_number)

        new_data = {
            CONF_REGION: REGION_VA_NC,
            CONF_USERNAME: self._username,
            CONF_PASSWORD: self._password,
            CONF_ACCESS_TOKEN: self._access_token,
            CONF_REFRESH_TOKEN: self._refresh_token,
            CONF_COOKIES: self._cookies,
            CONF_ACCOUNT_NUMBER: account.account_number,
            CONF_METER_NUMBER: meter.device_id,
            CONF_SERVICE_ADDRESS: str(account.service_address),
        }

        # Handle reconfigure flow - update existing entry
        if self.source == SOURCE_RECONFIGURE:
            self._abort_if_unique_id_mismatch()
            return self.async_update_reload_and_abort(
                self._get_reconfigure_entry(),
                data=new_data,
            )

        # New entry - check for duplicates
        self._abort_if_unique_id_configured()

        return self.async_create_entry(
            title=f"Dominion Energy ({account.account_number})",
            data=new_data,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle reconfiguration - allows user to re-authenticate."""
        if is_sc(self._get_reconfigure_entry().data):
            return await self.async_step_sc_credentials()
        return await self.async_step_credentials()

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Handle reauth when tokens expire."""
        self._region = get_region(entry_data)
        return await self.async_step_reauth_confirm()

    async def _async_submit_login(
        self, reauth_entry: ConfigEntry, *, reuse_saved: bool
    ) -> LoginResult:
        """Start a login for the reauth entry's region.

        Args:
            reauth_entry: The entry to re-authenticate.
            reuse_saved: Use the saved cookies (VA/NC) or TFA token (SC)
                to try to skip TFA.
        """
        assert self._username is not None
        assert self._password is not None

        # TFA targets belong to one login session
        self._tfa_targets = []
        self._selected_tfa_target = None

        if self._region == REGION_SC:
            sc_authenticator = self._new_sc_authenticator()
            self._authenticator = sc_authenticator
            tfa_token = reauth_entry.data.get(CONF_TFA_TOKEN) if reuse_saved else None
            return await sc_authenticator.async_submit_credentials(
                self._username, self._password, tfa_token
            )

        authenticator = GigyaAuthenticator(async_get_clientsession(self.hass))
        self._authenticator = authenticator
        existing_cookies = reauth_entry.data.get(CONF_COOKIES)
        if reuse_saved and existing_cookies:
            # Load existing cookies to potentially bypass TFA
            authenticator.import_cookies(existing_cookies)
        await authenticator.async_init_session()
        return await authenticator.async_submit_credentials(
            self._username, self._password
        )

    async def _async_update_reauth_entry(
        self, reauth_entry: ConfigEntry, tokens: TokenPair | None
    ) -> ConfigFlowResult:
        """Finish the login and update the reauth entry.

        Args:
            reauth_entry: The entry to update.
            tokens: VA/NC tokens from TFA verification. If None, the login
                is completed here.
        """
        if isinstance(self._authenticator, SCAuthenticator):
            sc_session = await self._authenticator.async_complete_login()
            if sc_account_key(sc_session.account.user_id) != reauth_entry.unique_id:
                return self.async_abort(reason="wrong_account")
            new_data = {
                **reauth_entry.data,
                CONF_USERNAME: self._username,
                CONF_PASSWORD: self._password,
                CONF_TFA_TOKEN: self._authenticator.tfa_token,
                CONF_METER_TYPES: [
                    meter_type.value for meter_type in sc_session.account.meter_types
                ],
            }
            return self.async_update_reload_and_abort(reauth_entry, data=new_data)

        assert isinstance(self._authenticator, GigyaAuthenticator)
        if tokens is None:
            tokens = await self._authenticator._async_complete_login()
        customer_info = await DompowerClient(
            async_get_clientsession(self.hass),
            access_token=tokens.access_token,
            refresh_token=tokens.refresh_token,
        ).async_get_customer_info()
        account_numbers = {account.account_number for account in customer_info.accounts}
        if reauth_entry.data[CONF_ACCOUNT_NUMBER] not in account_numbers:
            return self.async_abort(reason="wrong_account")
        new_data = {
            **reauth_entry.data,
            CONF_USERNAME: self._username,
            CONF_PASSWORD: self._password,
            CONF_ACCESS_TOKEN: tokens.access_token,
            CONF_REFRESH_TOKEN: tokens.refresh_token,
            CONF_COOKIES: self._authenticator.export_cookies(),
        }
        return self.async_update_reload_and_abort(reauth_entry, data=new_data)

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle reauth confirmation - attempt auto-login with stored credentials."""
        errors: dict[str, str] = {}
        reauth_entry = self._get_reauth_entry()
        self._region = get_region(reauth_entry.data)

        # Get stored credentials
        stored_username = reauth_entry.data.get(CONF_USERNAME)
        stored_password = reauth_entry.data.get(CONF_PASSWORD)

        # Auto-login with stored credentials if there is no user input yet.
        # Otherwise, use the credentials that the user submitted.
        attempt: tuple[str, str, bool] | None = None
        if user_input is None and stored_username and stored_password:
            attempt = (stored_username, stored_password, True)
        elif user_input is not None:
            attempt = (user_input[CONF_USERNAME], user_input[CONF_PASSWORD], False)

        if attempt is not None:
            self._username, self._password, reuse_saved = attempt
            try:
                result = await self._async_submit_login(
                    reauth_entry, reuse_saved=reuse_saved
                )
                if result.tfa_required:
                    return await self.async_step_reauth_tfa_select()
                return await self._async_update_reauth_entry(reauth_entry, None)

            except InvalidCredentialsError:
                errors["base"] = "invalid_credentials"
            except CannotConnectError:
                errors["base"] = "cannot_connect"
            except ApiError as err:
                _LOGGER.error("Reauth API error: %s", err)
                errors["base"] = _sc_api_error_key(err)
            except (GigyaError, InvalidAuthError) as err:
                _LOGGER.error("Reauth authentication error: %s", err)
                errors["base"] = "unknown"
            except Exception:
                _LOGGER.exception("Unexpected exception during reauth")
                errors["base"] = "unknown"

        return self._show_reauth_form(errors)

    def _show_reauth_form(self, errors: dict[str, str]) -> ConfigFlowResult:
        """Show the reauth credentials form."""
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
            description_placeholders={"name": self._get_reauth_entry().title},
        )

    async def async_step_reauth_tfa_select(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle TFA target selection during reauth."""
        errors: dict[str, str] = {}

        if self._authenticator is None:
            return self.async_abort(reason="unknown")

        # Get TFA options if we don't have them yet
        if not self._tfa_targets:
            try:
                self._tfa_targets = await self._authenticator.async_get_tfa_options()
            except (GigyaError, ApiError, CannotConnectError) as err:
                # Show the form. Calling async_step_reauth_confirm logs in
                # with the saved credentials again and returns here.
                _LOGGER.error("Failed to get TFA options: %s", err)
                return self._show_reauth_form({"base": "cannot_connect"})

        if not self._tfa_targets:
            _LOGGER.error("No TFA targets available")
            return self._show_reauth_form({"base": "unknown"})

        if user_input is not None:
            selected_key = user_input["tfa_target"]

            for target in self._tfa_targets:
                if target.id == selected_key:
                    self._selected_tfa_target = target
                    break

            if self._selected_tfa_target:
                try:
                    await self._authenticator.async_send_tfa_code(
                        self._selected_tfa_target
                    )
                    return await self.async_step_reauth_tfa_code()
                except (GigyaError, ApiError, CannotConnectError) as err:
                    _LOGGER.error("Failed to send TFA code: %s", err)
                    errors["base"] = "unknown"

        options: dict[str, str] = {}
        for target in self._tfa_targets:
            options[target.id] = target.obfuscated

        return self.async_show_form(
            step_id="reauth_tfa_select",
            data_schema=vol.Schema(
                {
                    vol.Required("tfa_target"): vol.In(options),
                }
            ),
            errors=errors,
        )

    async def async_step_reauth_tfa_code(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle TFA code entry during reauth."""
        errors: dict[str, str] = {}
        reauth_entry = self._get_reauth_entry()

        if self._authenticator is None:
            return self.async_abort(reason="unknown")

        if user_input is not None:
            code = user_input["code"]

            try:
                if isinstance(self._authenticator, SCAuthenticator):
                    await self._authenticator.async_verify_tfa_code(code)
                    return await self._async_update_reauth_entry(reauth_entry, None)

                tokens = await self._authenticator.async_verify_tfa_code(code)
                return await self._async_update_reauth_entry(reauth_entry, tokens)

            except TFAVerificationError:
                errors["base"] = "tfa_failed"
            except TFAExpiredError:
                errors["base"] = "tfa_expired"
                self._tfa_targets = []
                return await self.async_step_reauth_tfa_select()
            except CannotConnectError:
                errors["base"] = "cannot_connect"
            except ApiError as err:
                _LOGGER.error("API error after TFA: %s", err)
                errors["base"] = _sc_api_error_key(err)
            except (GigyaError, InvalidAuthError) as err:
                _LOGGER.error("TFA verification error: %s", err)
                errors["base"] = "unknown"

        target_display = (
            self._selected_tfa_target.obfuscated
            if self._selected_tfa_target
            else "your device"
        )

        return self.async_show_form(
            step_id="reauth_tfa_code",
            data_schema=STEP_TFA_CODE_SCHEMA,
            errors=errors,
            description_placeholders={"target": target_display},
        )


def _sc_api_error_key(err: ApiError) -> str:
    """Return the form error key for an ApiError during login."""
    if "more than one account" in str(err):
        return "multiple_accounts"
    return "cannot_connect"


class DominionEnergyOptionsFlow(OptionsFlow):
    """Handle options flow for Dominion Energy."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize options flow."""
        self._config_entry = config_entry
        self._selected_mode: str | None = None

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 1: Select cost calculation mode."""
        if user_input is not None:
            self._selected_mode = user_input[CONF_COST_MODE]

            if self._selected_mode == COST_MODE_FIXED:
                return await self.async_step_fixed_rate()
            if self._selected_mode == COST_MODE_TOU:
                return await self.async_step_tou()
            if self._selected_mode == COST_MODE_SCHEDULE_1:
                return await self.async_step_schedule1()

            # API Estimate — no additional config needed
            return self.async_create_entry(
                title="", data={CONF_COST_MODE: COST_MODE_API}
            )

        current_options = self._config_entry.options

        cost_modes = {
            COST_MODE_API: "API Estimate (from bill)",
            COST_MODE_FIXED: "Fixed Rate",
            COST_MODE_TOU: "Time-of-Use",
            COST_MODE_SCHEDULE_1: "Schedule 1 - VA Residential",
        }
        if is_sc(self._config_entry.data):
            # Schedule 1 is a Virginia tariff
            del cost_modes[COST_MODE_SCHEDULE_1]
        default_mode = current_options.get(CONF_COST_MODE, COST_MODE_API)
        if default_mode not in cost_modes:
            default_mode = COST_MODE_API

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_COST_MODE, default=default_mode): vol.In(
                        cost_modes
                    ),
                }
            ),
        )

    async def async_step_fixed_rate(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 2a: Configure fixed rate."""
        if user_input is not None:
            return self.async_create_entry(
                title="",
                data={
                    CONF_COST_MODE: COST_MODE_FIXED,
                    CONF_FIXED_RATE: user_input[CONF_FIXED_RATE],
                },
            )

        current_options = self._config_entry.options

        return self.async_show_form(
            step_id="fixed_rate",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_FIXED_RATE,
                        default=current_options.get(
                            CONF_FIXED_RATE, DEFAULT_FIXED_RATE
                        ),
                    ): vol.Coerce(float),
                }
            ),
        )

    async def async_step_tou(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 2b: Configure time-of-use rates."""
        if user_input is not None:
            return self.async_create_entry(
                title="",
                data={
                    CONF_COST_MODE: COST_MODE_TOU,
                    CONF_PEAK_RATE: user_input[CONF_PEAK_RATE],
                    CONF_OFF_PEAK_RATE: user_input[CONF_OFF_PEAK_RATE],
                    CONF_PEAK_START_HOUR: user_input[CONF_PEAK_START_HOUR],
                    CONF_PEAK_END_HOUR: user_input[CONF_PEAK_END_HOUR],
                },
            )

        current_options = self._config_entry.options

        return self.async_show_form(
            step_id="tou",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_PEAK_RATE,
                        default=current_options.get(CONF_PEAK_RATE, DEFAULT_PEAK_RATE),
                    ): vol.Coerce(float),
                    vol.Required(
                        CONF_OFF_PEAK_RATE,
                        default=current_options.get(
                            CONF_OFF_PEAK_RATE, DEFAULT_OFF_PEAK_RATE
                        ),
                    ): vol.Coerce(float),
                    vol.Required(
                        CONF_PEAK_START_HOUR,
                        default=current_options.get(
                            CONF_PEAK_START_HOUR, DEFAULT_PEAK_START_HOUR
                        ),
                    ): vol.All(vol.Coerce(int), vol.Range(min=0, max=23)),
                    vol.Required(
                        CONF_PEAK_END_HOUR,
                        default=current_options.get(
                            CONF_PEAK_END_HOUR, DEFAULT_PEAK_END_HOUR
                        ),
                    ): vol.All(vol.Coerce(int), vol.Range(min=0, max=23)),
                }
            ),
        )

    async def async_step_schedule1(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 2c: Confirm Schedule 1 selection."""
        if user_input is not None:
            return self.async_create_entry(
                title="", data={CONF_COST_MODE: COST_MODE_SCHEDULE_1}
            )

        return self.async_show_form(
            step_id="schedule1",
            data_schema=vol.Schema({}),
            description_placeholders={
                "schedule_name": VA_SCHEDULE_1.name,
                "effective_date": VA_SCHEDULE_1.effective_date,
            },
        )
