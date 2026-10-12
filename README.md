<p align="center">
  <img src="assets/jaws-icon-raster-128.png" alt="Dominion Energy Integration Logo" width="128" height="128">
</p>

<h1 align="center">Dominion Energy for Home Assistant</h1>

<p align="center">
  <a href="https://github.com/YeomansIII/ha-dominion-energy/releases"><img src="https://img.shields.io/github/v/release/YeomansIII/ha-dominion-energy?style=flat-square" alt="GitHub Release"></a>
  <a href="https://github.com/YeomansIII/ha-dominion-energy/blob/main/LICENSE"><img src="https://img.shields.io/github/license/YeomansIII/ha-dominion-energy?style=flat-square" alt="License"></a>
  <a href="https://github.com/YeomansIII/ha-dominion-energy/issues"><img src="https://img.shields.io/github/issues/YeomansIII/ha-dominion-energy?style=flat-square" alt="Issues"></a>
  <img src="https://img.shields.io/badge/Home%20Assistant-blue?style=flat-square&logo=homeassistant&logoColor=white" alt="Home Assistant">
  <a href="https://github.com/hacs/integration"><img src="https://img.shields.io/badge/HACS-Custom-orange?style=flat-square" alt="HACS"></a>
</p>

<p align="center">
  Monitor your Dominion Energy electricity usage in Home Assistant with high-resolution interval data.
  Supports Virginia, North Carolina, and South Carolina.
</p>

---

## Features

- 30-minute interval energy usage data (Virginia and North Carolina)
- 15-minute electric data, solar generation, and hourly gas data (South Carolina, [beta](#south-carolina-beta))
- Solar generation (energy sent to the grid) for net metering accounts
- Daily and monthly usage totals
- Cost estimation with multiple calculation modes:
  - **API Estimate**: Derives rate from your actual bill (charges / usage)
  - **Fixed Rate**: Single $/kWh rate
  - **Time-of-Use**: Peak and off-peak rates by hour
- Full Energy Dashboard compatibility
- Automatic token refresh

## Installation

### HACS (Recommended)

1. Open HACS in Home Assistant
2. Click the three dots in the top right corner
3. Select "Custom repositories"
4. Add this repository URL and select "Integration" as the category
5. Click "Add"
6. Search for "Dominion Energy" and install it
7. Restart Home Assistant

### Manual Installation

1. Copy the `custom_components/dominion_energy` folder to your Home Assistant `config/custom_components/` directory
2. Restart Home Assistant

## Setup

### Add Integration

1. Go to Home Assistant Settings > Devices & Services
2. Click "Add Integration"
3. Search for "Dominion Energy"
4. Select your service area: **Virginia or North Carolina**, or **South Carolina**
5. Enter your Dominion Energy username and password
6. Complete the two-factor authentication (TFA) when prompted
7. Select your account and meter if you have multiple (Virginia and North Carolina only)

> **Note**: SMS-based TFA is recommended. Email TFA may have reliability issues.

### Configure Cost Calculation (Optional)

1. After setup, click "Configure" on the integration
2. Choose your cost calculation method:
   - **API Estimate**: Uses your actual bill rate (recommended)
   - **Fixed Rate**: Enter a single $/kWh rate
   - **Time-of-Use**: Configure peak/off-peak rates and hours

## Sensors

| Sensor | Description | State Class |
|--------|-------------|-------------|
| Latest Interval Usage | Most recent 30-minute reading (kWh) | measurement |
| Yesterday's Usage | Previous day's total consumption (kWh) | total_increasing |
| Current Month Usage | Month-to-date consumption (kWh) | total_increasing |
| Yesterday's Generation | Solar energy sent to the grid on the previous day (kWh) | total_increasing |
| Current Month Generation | Month-to-date solar energy sent to the grid (kWh) | total_increasing |
| Yesterday's Cost | Estimated cost for previous day ($) | total |
| Current Month Cost | Estimated cost for month-to-date ($) | total |
| Current Billing Period Usage | Usage in current billing cycle (kWh) | total_increasing |
| Last Bill Charges | Charges from previous bill ($) | total |
| Last Bill Usage | Usage from previous bill (kWh) | total |
| Effective Rate | Derived cost per kWh ($/kWh) | measurement |

South Carolina entries do not have the Last Bill and Current Billing Period Usage sensors. They have these sensors:

| Sensor | Description | State Class |
|--------|-------------|-------------|
| Bill Cost to Date | Cost in the current billing period so far ($, electric and gas together) | total |
| Forecasted Bill | Forecast for the full billing period ($) | total |
| Same Period Last Year | Cost for the same period last year ($), if known | total |
| Yesterday's Gas Usage | Previous day's gas use (ft³), gas accounts only | total_increasing |
| Current Month Gas Usage | Month-to-date gas use (ft³), gas accounts only | total_increasing |

> **Note**: The Dominion Energy API only provides data for **completed days**. Yesterday's data typically becomes available the following morning. Sensors include a `data_date` attribute showing which day the data represents.

## Energy Dashboard

This integration provides **external statistics** for the Home Assistant Energy Dashboard with hourly granularity.

### Setup

1. Go to **Settings → Dashboards → Energy**
2. Under **Electricity grid**, click **Add consumption**
3. Search for your account number or "dominion"
4. Select the statistic: `dominion_energy:{account_number}_energy_consumption`
5. For cost tracking, select **"Use an entity tracking the total costs"**
6. Select the cost statistic: `dominion_energy:{account_number}_energy_cost`

### Available Statistics

| Statistic ID | Description |
|--------------|-------------|
| `dominion_energy:{account}_energy_consumption` | Cumulative energy consumption (kWh) |
| `dominion_energy:{account}_energy_generation` | Cumulative solar energy sent to the grid (kWh). Accounts with solar only. Use it under **Return to grid**. |
| `dominion_energy:{account}_energy_cost` | Cumulative energy cost (uses configured cost mode) |
| `dominion_energy:{account}_gas_consumption` | Cumulative gas use (ft³). South Carolina accounts with gas only. Use it under **Gas consumption**. |

For South Carolina, `{account}` is `sc_` followed by an internal account ID. Search for "dominion" to find the statistics. Their names include your service address.

### How It Works

- The integration creates external statistics (not sensor entities) for the Energy Dashboard
- Data is aggregated from 30-minute intervals into hourly statistics
- **60 days of historical data** are automatically backfilled on first setup
- Cost is calculated using your configured cost mode (API estimate, fixed rate, or TOU)
- Statistics update daily with the previous day's data

### Why External Statistics?

The Energy Dashboard works best with cumulative statistics that track total consumption over time. External statistics allow the integration to:
- Backfill historical data that existed before you installed the integration
- Provide accurate hourly breakdowns for energy analysis
- Handle the 1-day data delay gracefully

> **Tip**: You can find your account number in the integration's device info or on your Dominion Energy bill.

## Authentication

Tokens automatically refresh in the background. If authentication fails:

1. Home Assistant will show a notification to re-authenticate
2. Click the notification to start the re-authentication flow
3. Enter your username/password and complete TFA again

## Troubleshooting

### "Cannot connect to API"
- Check your internet connection
- Verify Dominion Energy services are online

### "Invalid authentication"
- Tokens may have expired after extended inactivity
- Use the re-authentication flow to log in again

### Missing data
- Data may take up to 30 minutes to appear after setup
- Historical data availability depends on Dominion Energy's API

## South Carolina (beta)

Dominion Energy South Carolina uses a different website (`account.dominionenergysc.com`) and a different data provider (Bidgely). This integration supports it, but the maintainer does not have an SC account. If something does not work, please report it in [issue #19](https://github.com/YeomansIII/ha-dominion-energy/issues/19).

- Use the username and password for `account.dominionenergysc.com`.
- TFA is necessary on the first login. The integration saves a device token, so later logins do not ask for a code. If Dominion rejects the token, Home Assistant asks you to re-authenticate.
- Data updates every 2 hours. Dominion adds each day's data the next day.
- Only logins with one service account are supported.
- The **API Estimate** cost mode derives a rate from your current bill only for electric-only accounts. The SC bill includes gas, so gas accounts use the fixed rate. The **Schedule 1** mode is not available, because it is a Virginia tariff.
- Gas has usage statistics but no cost statistics.

To report a problem, you can attach a diagnostic report to the issue. On a computer with [uv](https://docs.astral.sh/uv/), run these commands. Use your SC portal username. It is not always your email address.

```bash
uvx dompower sc-login --username your-username
uvx dompower sc-report
```

This writes `dompower-sc-report.txt`. The file does not contain your username, password, TFA token, email address, phone number, address, or account number. It does contain your meter readings and bill amounts. Read the file before you attach it.

## API Constants

The Dominion Energy API uses SAP Customer Data Cloud (Gigya) for authentication. The following API key is the default for all users:

```
GIGYA_API_KEY = "4_6zEg-HY_0eqpgdSONYkJkQ"
```

This is a public client identifier embedded in the Dominion Energy web app. It can be overridden via the `GIGYA_API_KEY` environment variable if Dominion updates it.

## Support

- [Report Issues](https://github.com/YeomansIII/ha-dominion-energy/issues)
- [dompower Library](https://github.com/YeomansIII/dompower)

## License

MIT License
