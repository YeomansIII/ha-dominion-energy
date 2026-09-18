# South Carolina support plan

Tracks the work for [issue #19](https://github.com/YeomansIII/ha-dominion-energy/issues/19):
add Dominion Energy South Carolina (SC) accounts to `dompower` and to this integration.

Branch in both repos: `feat/south-carolina`.

## Background

SC does not use the VA/NC platform. It uses a different portal and a different data vendor.

| Item | VA/NC | SC |
|---|---|---|
| Login | Gigya SSO, then SEW JWT tokens | ASP.NET portal at `account.dominionenergysc.com/fusionapi/...` |
| TFA | Gigya phone or email | `InitAuthentication` → `SendPINCode` → `VerifyPIN` |
| Remember device | Gigya cookies | `tfa_token` from `VerifyPIN` (with `registerDevice: true`) |
| Usage data | SEW Excel, 30-minute intervals | Bidgely Green Button XML, 15-minute electric, 1-hour gas |
| Units | kWh | Wh (electric), ft³ (gas) |
| Solar | `generation` column in the Excel file | Separate meter register with negative values |
| Bill data | `billForecast` (last bill, current usage) | `GetAccountAMIUsageAlerts` (cost to date, forecast, last year) |
| Accounts | Many accounts and meters for each login | One account for each login (portal limit in the source code) |

Source code that we ported (Apache-2.0):

- <https://github.com/hype8912/dominion-sc-power> (library `dominionsc`)
- <https://github.com/hype8912/ha-dominion-sc> (HA integration)

Both are forks of `sctigercat1/ha-dominion-sc` and `sctigercat1/dominion-sc-power`.

## Step 1: dompower (`dompower/sc/`)

- [x] Add `dompower/sc/const.py` with the SC portal and Bidgely endpoints.
- [x] Add `dompower/sc/models.py` (`SCAccount`, `SCBillForecast`, `SCRegisterUsage`).
- [x] Add `dompower/sc/greenbutton.py`, a pure parser for the Green Button XML.
- [x] Add `dompower/sc/auth.py` (`SCAuthenticator`). It uses the same step
      method names as `GigyaAuthenticator`, so that the HA config flow can share steps.
- [x] Add `dompower/sc/client.py` (`DominionSCClient`).
- [x] Export the new names from `dompower/__init__.py`.
- [x] Add a `dompower sc-login` and `dompower sc-usage` CLI command for testers.
- [x] Add unit tests (parser, auth flow, client) with `aresponses`. (21 tests)
- [x] Add the Apache-2.0 attribution in `NOTICE`.
- [ ] Push the `feat/south-carolina` branch and open a PR. (Owner action.)
- [ ] Release `dompower` 0.3.0 to PyPI. See `RELEASING.md`. (Owner action.)

## Step 2: ha-dominion-energy

- [x] Add a region choice (VA/NC or SC) as the first config flow step.
- [x] Add the SC login, TFA, and reauth path. Reuse the TFA steps and strings.
- [x] Store `region`, `tfa_token`, and `meter_types` in the config entry.
- [x] Make the coordinator select the correct backend for each region.
- [x] Add SC statistics: electric consumption and cost, solar generation, gas.
- [x] Add SC sensors: cost to date, forecasted bill, last year's bill, generation, gas usage.
- [x] Show only the sensors that apply to the region.
- [x] Update `strings.json`, `README.md`, and `CLAUDE.md`.
- [x] Ship `translations/en.json` (it was missing, see gotchas).
- [x] Add unit tests that do not need Home Assistant (region helpers).
- [x] Add Home Assistant tests in `tests/ha` (config flow, SC and VA/NC coordinator).
- [x] Set `manifest.json` to `dompower==0.3.0`. This needs the dompower release first.
- [ ] Push the `feat/south-carolina` branch and open a PR. (Owner action.)
- [ ] Get a test run on a real SC account (ask @hype8912 or @bbacskay in issue #19).
- [ ] Follow-up: gas cost statistics ($/therm or $/ft³ options).
- [ ] Follow-up: support SC logins with more than one account.
- [ ] Follow-up: a pilot ID option, if some SC accounts need a different value.

## Test commands

```bash
# dompower
cd dompower && uv run pytest && uv run mypy dompower && uv run ruff check dompower tests

# ha-dominion-energy: pure tests only
cd ha-dominion-energy && uv run --extra dev pytest

# ha-dominion-energy: all tests (Home Assistant needs Python 3.13+)
cd ha-dominion-energy && uv run --python 3.13 --extra ha-test --with-editable ../dompower pytest
```

## Design decisions

1. **Separate backend, shared models.** SC code lives in `dompower/sc/`.
   It returns the existing `IntervalUsageData`, `TFATarget`, and `LoginResult` models.
   The HA coordinator then needs few changes for the interval pipeline.
2. **Solar split by sign.** For each 15-minute slot, positive values from all registers
   add to `consumption`. Negative values add (as absolute values) to `generation`.
   This works for a two-register account (grid plus export) and for a single net register.
   We do not use `flowDirection`, because SC sends `1` for both registers.
3. **Reuse exceptions.** `SCAuthenticator` raises `InvalidCredentialsError`,
   `TFARequiredError`, and `TFAVerificationError`. These are `GigyaError` subclasses.
   The name is not exact, but the HA config flow can then use the same `except` blocks.
4. **Log in on each update.** The SC portal session expires after some minutes.
   The coordinator logs in again at the start of each update. The stored `tfa_token` skips TFA.
5. **Poll less often for SC.** SC data changes once a day, and a login is 7 requests.
   SC entries update every 2 hours. VA/NC entries still update every 30 minutes.
6. **Stable SC identifier.** The config entry stores `sc_<bidgely user id>` as
   `account_number`. Statistic IDs, the unique ID, and the device use this value.
   The coordinator always uses the stored value, so a change on the server
   does not create new statistics.
7. **Statistic IDs match PR #18.** Solar uses `{account}_energy_generation`.
   This is the same ID that open PR #18 uses for VA/NC generation.
8. **Gas has no cost.** The cost options are in $/kWh. Gas gets consumption statistics
   (ft³) and sensors only.

## Gotchas

- **Own cookie jar.** The SC portal uses session cookies and a
  `__RequestVerificationToken`. Do not use the shared HA session.
  Use `async_create_clientsession(hass, cookie_jar=aiohttp.CookieJar(quote_cookie=False))`.
- **`quote_cookie=False` is necessary.** SC cookie values contain `=`. The default
  jar quotes them and the server then rejects the session.
- **Bidgely timestamps are local wall time.** The epoch values in the XML are local
  (Eastern) clock time encoded as if they were UTC. `1786579200` is 2026-08-13 00:00 UTC,
  but it means 00:00 EDT. The parser reads the UTC clock time and attaches `America/New_York`.
  During the DST fall-back hour, two readings can have the same local time.
  The HA code already merges these with `_deduplicate_hourly_by_utc`.
- **Request window.** The upstream code sends real epoch values for local midnight
  as `start` and `end`. We cannot be sure how Bidgely reads them. The client requests one
  extra day on each side and then filters the readings by local date.
- **Bidgely pilot ID.** The header `X-Bidgely-Pilot-Id` must be `10078`.
  The value `10106` gives smoothed, hourly, estimated data. The value can possibly
  be different for some accounts. `DominionSCClient` accepts a `pilot_id` override.
  The HA integration does not show this option yet.
- **One account for each login.** The upstream code rejects logins with more than one
  account (`singleAccount` is false). We do the same and raise `ApiError`.
  Multi-account support needs a real account to test.
- **Login always asks for TFA.** `Authenticate` returns `status: "twoFA"` for all
  valid logins. With a valid `tfa_token`, `Verify2FAToken` skips the code prompt.
  If the token is not accepted, the user must do TFA again (HA reauth).
- **TFA options are strings.** `InitAuthentication` returns lists of phone numbers
  and email addresses. The selected string is sent back as `sendMethod`.
- **Bill forecast covers electric and gas together.** For SC, the "API estimate"
  cost mode derives a rate from the forecast only for electric-only accounts.
  Other accounts use the fixed rate.
- **Gas units.** Gas values are ft³. Do not convert to therms in the library.
  The HA gas statistic uses `UnitOfVolume.CUBIC_FEET`.
- **Unmerged work.** dompower PR #7 (on `fix/token-expired-passthrough`) centralizes the
  user agent. SC has its own user agent in `dompower/sc/const.py` for now.
  HA PR #18 adds VA/NC generation statistics with the same statistic ID pattern.
  Expect merge conflicts in `coordinator.py` and `sensor.py`.
- **No SC account for the maintainer.** All SC code is tested with fixtures only.
  A real account test is necessary before release.
- **Unknown login status.** `Authenticate` can possibly return a status other than
  `failed` or `twoFA` (for example, for an account without TFA). We raise
  `InvalidAuthError` and do not guess. If a tester sees this, add the status.
- **DST fall-back hour.** Because the epochs are local wall time, the two
  01:00-01:59 hours in November can have the same timestamps. The client adds
  readings with the same timestamp together. It does not remove duplicates.
- **Gas days with no use.** Summer days can have zero gas use. The gas statistic
  keeps zero days. Electric consumption and generation still skip days that look
  incomplete (`_filter_incomplete_days`).
- **Generation statistic.** The coordinator adds `{account}_energy_generation` only
  when the current month has generation. Accounts without solar get no statistic.
  After a month with no generation, the next update fills the gap.
- **Many downloads for each SC update.** One SC update does a login (7 requests),
  the bill forecast (2 requests), and up to 6 Green Button downloads (yesterday,
  month, billing period, statistics, generation, gas). Each download asks for two
  extra days. This is why SC polls every 2 hours.
- **Sessions in the config flow.** Each SC login attempt in the config flow makes a
  new aiohttp session with its own cookie jar. Home Assistant closes these sessions
  when it stops.
- **Missing translations file.** Home Assistant loads UI text for custom integrations
  from `translations/en.json`, not `strings.json`. The repo did not have this file,
  so forms showed field keys. It now has a copy. Keep both files the same.
- **pre-commit mypy hook.** The hook installs `dompower` from PyPI (0.2.2), which
  does not have `dompower.sc`. It reports the SC names as missing. The HA commits on
  this branch used `SKIP=mypy`. Mypy passes with the local dompower branch.
  After the 0.3.0 release, the hook passes again.
- **`uv.lock` size.** The `ha-test` extra adds Home Assistant and its dependencies
  to `uv.lock` (about 7,900 lines).
- **Tests and `isinstance`.** The config flow uses `isinstance(..., SCAuthenticator)`.
  Tests must use `MagicMock(spec=SCAuthenticator)`, not a patched class.

## Test request for SC users

After the dompower branch is pushed, an SC user can run:

```bash
uvx --from git+https://github.com/YeomansIII/dompower@feat/south-carolina \
  dompower sc-login --username you@example.com
uvx --from git+https://github.com/YeomansIII/dompower@feat/south-carolina \
  dompower sc-usage --days 3
uvx --from git+https://github.com/YeomansIII/dompower@feat/south-carolina \
  dompower sc-usage --days 3 --gas
```

Ask the tester to check and report:

1. `sc-login` completes TFA and a second `sc-login` does not ask for a code.
2. The register list: how many registers, the interval length, and if one has
   only negative values (solar export).
3. The first and last timestamps. The first should be 00:00 on the first day,
   in local time.
4. The daily totals compared with the usage graph on the SC website.
5. The bill forecast compared with the SC website.
