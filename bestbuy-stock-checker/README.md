# Best Buy Stock Checker — Mac Studio M4 Max

Monitors Best Buy for **NEW** inventory of the Apple Mac Studio M4 Max 512GB Silver.
Open-box inventory is logged but **never triggers alerts**.

## Quick Start

```bash
cd bestbuy-stock-checker

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Install Playwright browsers (optional — only needed if static HTML gets blocked)
playwright install chromium

# Configure
cp .env.example .env
# Edit .env with your credentials
```

## Configuration

Edit `.env` to enable notification channels:

| Variable | Description |
|---|---|
| `BESTBUY_URL` | Product page URL |
| `BESTBUY_SKU` | SKU ID (strongest identifier) |
| `ZIP_CODE` | For shipping/pickup detection (default: 28217) |
| `STORE_NAME` | Store for pickup (default: Concord Mills) |
| `CHECK_INTERVAL_SECONDS` | Polling interval (default: 300) |
| `SMTP_ENABLED` | Enable email alerts |
| `DISCORD_ENABLED` | Enable Discord webhook |
| `TWILIO_ENABLED` | Enable SMS via Twilio |

## Running

### Single check
```bash
python3 stock_checker.py --once
```

### Continuous loop (default)
```bash
python3 stock_checker.py --loop
# or simply:
python3 stock_checker.py
```

### Using the helper script
```bash
chmod +x run.sh
./run.sh              # continuous loop
./run.sh --once       # single check
./run.sh --test-notify # test notifications
```

### Test notifications
```bash
python3 stock_checker.py --test-notify
```
This sends a test message to all enabled channels (email, Discord, SMS, desktop) so you can verify they work before going live.

## Testing Twilio SMS Safely

1. Set up your `.env` with Twilio credentials:
   ```
   TWILIO_ENABLED=true
   TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
   TWILIO_AUTH_TOKEN=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
   TWILIO_FROM_NUMBER=+1XXXXXXXXXX
   TWILIO_TO_NUMBER=+17047736226
   ```
2. Run: `python3 stock_checker.py --test-notify`
3. You'll receive a test SMS. Check your phone.
4. Twilio trial accounts can only send to verified numbers — verify your number at twilio.com/console first.

## Running as a Background Service (macOS launchd)

1. Edit `com.bestbuy.stockchecker.plist`:
   - Replace `/Users/YOURUSERNAME/` with your actual home path
   - Update the Python path if using a venv: `<string>/Users/YOU/IXON/bestbuy-stock-checker/venv/bin/python3</string>`

2. Install:
   ```bash
   cp com.bestbuy.stockchecker.plist ~/Library/LaunchAgents/
   launchctl load ~/Library/LaunchAgents/com.bestbuy.stockchecker.plist
   ```

3. Check status:
   ```bash
   launchctl list | grep bestbuy
   tail -f launchd_stdout.log
   ```

4. Stop:
   ```bash
   launchctl unload ~/Library/LaunchAgents/com.bestbuy.stockchecker.plist
   ```

## Monitoring a Different Product

Change these in `.env` (or pass via CLI):

```bash
BESTBUY_URL=https://www.bestbuy.com/site/some-other-product/1234567.p?skuId=1234567
BESTBUY_SKU=1234567
```

Or via CLI:
```bash
python3 stock_checker.py --url "https://www.bestbuy.com/site/..." --sku "1234567" --once
```

## Detection Logic

### Selectors used for product pages

| Data | Selectors / Patterns |
|---|---|
| Product title | `h1.heading`, `h1[class*='heading']`, `.sku-title h1` |
| Price | `[data-testid='customer-price'] span`, `.priceView-customer-price span`, JSON `"currentPrice"` |
| Add to Cart button | `button.add-to-cart-button`, `[data-testid='add-to-cart-button']`, `button[data-button-state='ADD_TO_CART']` |
| Button state | `data-button-state` attribute, embedded JSON `"buttonState"` |
| Availability | JSON-LD `offers.availability`, fulfillment summary text |
| Open-box | `[class*='open-box']`, `[data-testid*='open-box']`, text matching |
| SKU | `"skuId"` in page JSON, `[data-sku-id]` attribute |

### How NEW vs open-box is distinguished

- The main Add to Cart button and fulfillment text are for NEW product
- Open-box has separate DOM sections with `open-box` in class names
- Only NEW status changes trigger alerts
- Open-box changes are logged with `NO ALERT` marker

### Location awareness

- ZIP code 28217 is set via cookie when using Playwright
- Shipping/pickup detection relies on fulfillment summary text containing "ship", "delivery", "pick up", "store"
- If Best Buy doesn't expose location-specific data in static HTML, the checker still monitors the general add-to-cart / availability state

## Output Files

| File | Purpose |
|---|---|
| `stock_checker.log` | Full log of every check |
| `stock_log.csv` | CSV log for analysis |
| `state.json` | Last known state (prevents duplicate alerts) |
| `screenshots/` | Playwright screenshots (when Playwright is used) |

## Alert Rules

**Alerts fire when:**
- `unavailable` → NEW available
- `sold_out` → NEW available
- NEW shipping/pickup becomes available
- NEW price changes by more than $5

**Alerts do NOT fire when:**
- Only open-box becomes available
- Open-box price changes
- State stays the same
