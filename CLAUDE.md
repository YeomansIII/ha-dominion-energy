# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a Home Assistant custom integration for monitoring Dominion Energy electricity usage. It provides 30-minute interval data, daily/monthly totals, and cost estimation with multiple calculation modes.

It supports two regions (`CONF_REGION` in the entry data):
- **VA/NC** (`va_nc`, and all entries without a region): Gigya login, `DompowerClient`, 30-minute data.
- **SC** (`sc`): SC portal login, `DominionSCClient` from `dompower.sc`, 15-minute electric, solar generation, hourly gas. See `docs/south-carolina-plan.md` for design decisions and gotchas.

## Architecture

### Key Components

- **`coordinator.py`**: `DominionEnergyCoordinator` extends Home Assistant's `DataUpdateCoordinator`. Manages API calls via the `dompower` library, handles token refresh callbacks, and calculates costs based on configured mode (API estimate, fixed rate, or time-of-use).

- **`config_flow.py`**: Setup flow:
  1. `user` step selects the region.
  2. VA/NC: `credentials` → Gigya login → `tfa_select`/`tfa_code` → account/meter discovery → `select_meter` if there are many.
  3. SC: `sc_credentials` → `SCAuthenticator` → the same `tfa_select`/`tfa_code` steps → entry with `tfa_token`, `meter_types`, and `account_number = sc_<bidgely user id>`.

  Also implements `OptionsFlow` for cost calculation configuration and reauth flow for expired tokens.

- **`sensor.py`**: Defines sensor entities using `DominionEnergySensorDescription` dataclass with `value_fn` lambdas for extracting data. Sensors include usage metrics, costs, and bill forecast data.

- **`const.py`**: Configuration keys, cost mode constants, and default values.

- **`region.py`**: Region helpers with no Home Assistant imports (`get_region`, `sc_account_key`, `sc_derived_rate`).

- **`translations/en.json`**: Copy of `strings.json`. Custom integrations load UI text from this file. Keep both files the same.

### Data Flow

1. Config flow validates tokens via `DompowerClient.async_refresh_tokens()`
2. Coordinator fetches interval usage data every 30 minutes
3. Cost calculation uses one of three modes from options: `api_estimate` (derived from bill), `fixed`, or `time_of_use`
4. Sensors read from `DominionEnergyData` dataclass returned by coordinator

### External Dependencies

- **`dompower`**: Python library for Dominion Energy API (required version in `manifest.json`)
- Uses SAP Customer Data Cloud (Gigya) for authentication

## Development Notes

### Home Assistant Integration Patterns Used

- `ConfigEntry.runtime_data` for storing coordinator instance
- Type alias: `type DominionEnergyConfigEntry = ConfigEntry[DominionEnergyCoordinator]`
- Token persistence via `hass.config_entries.async_update_entry()` in callback
- `ConfigEntryAuthFailed` exception triggers reauth flow

### Testing

```bash
# Pure tests (rates, region). tests/ha is skipped.
uv run --extra dev pytest

# All tests, including Home Assistant tests in tests/ha. Home Assistant needs
# Python 3.13+. Until dompower with dompower.sc is on PyPI, use the local copy.
uv run --python 3.13 --extra ha-test --with-editable ../dompower pytest
```

- `tests/ha` uses pytest-homeassistant-custom-component with a test recorder.
- Patch `DominionEnergyConfigFlow._new_sc_authenticator` with `MagicMock(spec=SCAuthenticator)`, so the `isinstance` checks in the flow still work.
- Patch `coordinator.async_add_external_statistics` to inspect statistics.
- The pre-commit mypy hook installs `dompower` from PyPI. Until a `dompower` release has `dompower.sc`, it reports missing SC names.

### HACS Distribution

- `hacs.json` configures HACS metadata
- `manifest.json` must be kept in sync with actual `dompower` version requirements
