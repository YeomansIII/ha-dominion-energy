# Dominion Energy Home Assistant Integration

A Home Assistant custom integration for monitoring your Dominion Energy electricity usage with high-resolution 30-minute interval data.

## Features

- 30-minute interval energy usage data
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
4. Enter your Dominion Energy username (email) and password
5. Complete the two-factor authentication (TFA) when prompted
6. Select your account and meter if you have multiple

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
| Yesterday's Cost | Estimated cost for previous day ($) | total |
| Current Month Cost | Estimated cost for month-to-date ($) | total |
| Current Billing Period Usage | Usage in current billing cycle (kWh) | total_increasing |
| Last Bill Charges | Charges from previous bill ($) | total |
| Last Bill Usage | Usage from previous bill (kWh) | total |
| Effective Rate | Derived cost per kWh ($/kWh) | measurement |

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
| `dominion_energy:{account}_energy_cost` | Cumulative energy cost (uses configured cost mode) |

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
