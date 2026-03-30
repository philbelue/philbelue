#!/usr/bin/env python3
"""
Best Buy Stock Checker — Apple Mac Studio M4 Max 512GB Silver
Monitors NEW inventory only. Open-box is logged but never triggers alerts.
"""

import argparse
import csv
import json
import logging
import os
import platform
import random
import re
import smtplib
import subprocess
import sys
import time
from datetime import datetime
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PRODUCT_NAME = "Apple Mac Studio - M4 Max - 512GB SSD - Silver"
DEFAULT_URL = os.getenv(
    "BESTBUY_URL",
    "https://www.bestbuy.com/site/apple-mac-studio-desktop-apple-m4-max-chip-48gb-memory-512gb-ssd-latest-model-silver/6604920.p?skuId=6604920",
)
DEFAULT_SKU = os.getenv("BESTBUY_SKU", "6604920")
ZIP_CODE = os.getenv("ZIP_CODE", "28217")
STORE_NAME = os.getenv("STORE_NAME", "Concord Mills")

CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL_SECONDS", "300"))
JITTER_MIN = int(os.getenv("JITTER_MIN", "15"))
JITTER_MAX = int(os.getenv("JITTER_MAX", "45"))

STATE_FILE = os.getenv("STATE_FILE", "state.json")
LOG_FILE = os.getenv("LOG_FILE", "stock_checker.log")
CSV_LOG_FILE = os.getenv("CSV_LOG_FILE", "stock_log.csv")
SCREENSHOT_DIR = Path("screenshots")

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.4; rv:125.0) Gecko/20100101 Firefox/125.0",
]

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ],
)
log = logging.getLogger("bestbuy")

# ---------------------------------------------------------------------------
# State Store
# ---------------------------------------------------------------------------


class StateStore:
    """Persist last known product state to a local JSON file."""

    def __init__(self, path: str = STATE_FILE):
        self.path = Path(path)
        self._state: dict = {}
        self._load()

    def _load(self):
        if self.path.exists():
            try:
                self._state = json.loads(self.path.read_text())
            except (json.JSONDecodeError, OSError):
                self._state = {}

    def save(self):
        self.path.write_text(json.dumps(self._state, indent=2, default=str))

    def get(self, key: str, default=None):
        return self._state.get(key, default)

    def set(self, key: str, value):
        self._state[key] = value
        self.save()

    def update(self, data: dict):
        self._state.update(data)
        self.save()


# ---------------------------------------------------------------------------
# Notifier
# ---------------------------------------------------------------------------


class Notifier:
    """Send alerts via console, email, Discord, SMS (Twilio), desktop, sound."""

    def __init__(self):
        self.smtp_enabled = os.getenv("SMTP_ENABLED", "false").lower() == "true"
        self.discord_enabled = os.getenv("DISCORD_ENABLED", "false").lower() == "true"
        self.twilio_enabled = os.getenv("TWILIO_ENABLED", "false").lower() == "true"
        self.desktop_enabled = os.getenv("DESKTOP_NOTIFY", "true").lower() == "true"
        self.sound_enabled = os.getenv("SOUND_ALERT", "true").lower() == "true"

    # ---- console ----
    @staticmethod
    def console(message: str):
        log.info(message)

    # ---- email ----
    def send_email(self, subject: str, body: str):
        if not self.smtp_enabled:
            return
        try:
            msg = MIMEText(body)
            msg["Subject"] = subject
            msg["From"] = os.getenv("SMTP_USER", "")
            msg["To"] = os.getenv("SMTP_TO", "")
            with smtplib.SMTP(os.getenv("SMTP_HOST", "smtp.gmail.com"), int(os.getenv("SMTP_PORT", "587"))) as s:
                s.starttls()
                s.login(os.getenv("SMTP_USER", ""), os.getenv("SMTP_PASSWORD", ""))
                s.send_message(msg)
            log.info("Email sent successfully.")
        except Exception as e:
            log.error(f"Email failed: {e}")

    # ---- discord ----
    def send_discord(self, message: str):
        if not self.discord_enabled:
            return
        url = os.getenv("DISCORD_WEBHOOK_URL", "")
        if not url:
            return
        try:
            resp = requests.post(url, json={"content": message}, timeout=15)
            resp.raise_for_status()
            log.info("Discord notification sent.")
        except Exception as e:
            log.error(f"Discord failed: {e}")

    # ---- twilio SMS ----
    def send_sms(self, body: str):
        if not self.twilio_enabled:
            return
        try:
            from twilio.rest import Client

            client = Client(
                os.getenv("TWILIO_ACCOUNT_SID", ""),
                os.getenv("TWILIO_AUTH_TOKEN", ""),
            )
            msg = client.messages.create(
                body=body,
                from_=os.getenv("TWILIO_FROM_NUMBER", ""),
                to=os.getenv("TWILIO_TO_NUMBER", "+17047736226"),
            )
            log.info(f"SMS sent: SID={msg.sid}")
        except Exception as e:
            log.error(f"SMS failed: {e}")

    # ---- macOS desktop notification ----
    def send_desktop(self, title: str, message: str):
        if not self.desktop_enabled:
            return
        if platform.system() != "Darwin":
            return
        try:
            script = f'display notification "{message}" with title "{title}" sound name "Glass"'
            subprocess.run(["osascript", "-e", script], check=False, capture_output=True)
        except Exception:
            pass

    # ---- sound alert ----
    def play_sound(self):
        if not self.sound_enabled:
            return
        if platform.system() == "Darwin":
            subprocess.run(["afplay", "/System/Library/Sounds/Glass.aiff"], check=False, capture_output=True)

    # ---- unified alert (NEW inventory only) ----
    def alert(self, product: str, state_summary: str, price: str, url: str):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        body = (
            f"NEW INVENTORY ALERT\n"
            f"Product: {product}\n"
            f"Status: {state_summary}\n"
            f"Inventory: NEW (not open-box)\n"
            f"Price: {price}\n"
            f"URL: {url}\n"
            f"Timestamp: {ts}"
        )
        subject = f"IN STOCK (NEW): {product}"

        self.console(f"ALERT >>> {body}")
        self.send_email(subject, body)
        self.send_discord(f"**{subject}**\n```\n{body}\n```")
        self.send_sms(body)
        self.send_desktop(subject, f"{state_summary} — {price}")
        self.play_sound()

    # ---- test mode ----
    def test(self):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        test_msg = (
            f"TEST NOTIFICATION\n"
            f"Product: {PRODUCT_NAME}\n"
            f"Status: test-alert\n"
            f"Inventory: NEW (test)\n"
            f"Price: $1,999.00\n"
            f"URL: {DEFAULT_URL}\n"
            f"Timestamp: {ts}"
        )
        subject = f"[TEST] Stock Checker Notification"
        log.info("Sending test notifications to all enabled channels...")
        self.console(test_msg)
        self.send_email(subject, test_msg)
        self.send_discord(f"**{subject}**\n```\n{test_msg}\n```")
        self.send_sms(test_msg)
        self.send_desktop(subject, "Test notification from BestBuy Stock Checker")
        self.play_sound()
        log.info("Test notifications complete. Check each channel.")


# ---------------------------------------------------------------------------
# Product state result
# ---------------------------------------------------------------------------


class ProductState:
    """Encapsulate the result of a single product check."""

    def __init__(self):
        self.timestamp: str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.product_name: str = ""
        self.sku: str = ""
        self.url: str = ""
        self.price: str = "N/A"
        self.new_status: str = "unavailable"  # unavailable | sold_out | available_ship | available_pickup | add_to_cart | backorder | preorder
        self.open_box_status: str = "unavailable"  # unavailable | available
        self.open_box_price: str = "N/A"
        self.source: str = "static-html"  # static-html | playwright
        self.raw_availability_text: str = ""
        self.error: Optional[str] = None

    @property
    def new_available(self) -> bool:
        return self.new_status in ("available_ship", "available_pickup", "add_to_cart")

    def summary_line(self) -> str:
        inv_type = "NEW" if self.new_available else ("OPEN-BOX ONLY" if self.open_box_status == "available" else "NONE")
        return (
            f"Product: {self.product_name or PRODUCT_NAME} | "
            f"SKU: {self.sku} | "
            f"NEW Status: {self.new_status} | "
            f"Open-Box: {self.open_box_status} | "
            f"Inventory Type: {inv_type} | "
            f"Price: {self.price} | "
            f"Open-Box Price: {self.open_box_price} | "
            f"Source: {self.source}"
        )

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "product_name": self.product_name,
            "sku": self.sku,
            "url": self.url,
            "price": self.price,
            "new_status": self.new_status,
            "open_box_status": self.open_box_status,
            "open_box_price": self.open_box_price,
            "source": self.source,
            "raw_availability_text": self.raw_availability_text,
            "error": self.error,
        }


# ---------------------------------------------------------------------------
# CSV Logger
# ---------------------------------------------------------------------------


def log_csv(state: ProductState):
    file_exists = Path(CSV_LOG_FILE).exists()
    with open(CSV_LOG_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(state.to_dict().keys()))
        if not file_exists:
            writer.writeheader()
        writer.writerow(state.to_dict())


# ---------------------------------------------------------------------------
# BestBuyChecker
# ---------------------------------------------------------------------------


class BestBuyChecker:
    """Check Best Buy product page for NEW vs open-box inventory."""

    def __init__(self, url: str = DEFAULT_URL, sku: str = DEFAULT_SKU):
        self.url = url
        self.sku = sku
        self.session = requests.Session()
        self._update_headers()

    def _update_headers(self):
        self.session.headers.update({
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "DNT": "1",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Cache-Control": "max-age=0",
        })

    # ---- static HTML check ----
    def check_static(self) -> ProductState:
        state = ProductState()
        state.url = self.url
        state.sku = self.sku
        state.source = "static-html"

        try:
            self._update_headers()
            resp = self.session.get(self.url, timeout=30)
            resp.raise_for_status()
            html = resp.text

            # Check for blocking / captcha
            if self._is_blocked(html):
                state.error = "blocked-or-captcha"
                log.warning("Static request appears blocked. Will try Playwright fallback.")
                return state

            soup = BeautifulSoup(html, "lxml")
            self._parse_product_page(soup, html, state)

        except requests.RequestException as e:
            state.error = str(e)
            log.error(f"Static request failed: {e}")

        return state

    def _is_blocked(self, html: str) -> bool:
        blocked_signals = [
            "Access Denied",
            "Please verify you are a human",
            "captcha",
            "cf-browser-verification",
            "blocked",
            "Request unsuccessful",
        ]
        lower = html.lower()
        return any(sig.lower() in lower for sig in blocked_signals)

    def _parse_product_page(self, soup: BeautifulSoup, html: str, state: ProductState):
        """Parse a Best Buy product detail page."""

        # --- Product title ---
        title_el = soup.select_one("h1.heading, h1[class*='heading'], .sku-title h1, h1")
        if title_el:
            state.product_name = title_el.get_text(strip=True)

        # --- SKU from page ---
        sku_match = re.search(r'"skuId"\s*:\s*"(\d+)"', html)
        if sku_match:
            state.sku = sku_match.group(1)
        if not state.sku:
            sku_el = soup.select_one("[data-sku-id]")
            if sku_el:
                state.sku = sku_el.get("data-sku-id", "")

        # --- Price ---
        price_el = soup.select_one(
            "[data-testid='customer-price'] span, "
            ".priceView-customer-price span, "
            ".priceView-hero-price span, "
            "[class*='customerPrice'] span"
        )
        if price_el:
            state.price = price_el.get_text(strip=True)
        else:
            price_match = re.search(r'"currentPrice"\s*:\s*([\d.]+)', html)
            if price_match:
                state.price = f"${price_match.group(1)}"

        # --- Availability text ---
        avail_texts = []
        for sel in [
            ".fulfillment-add-to-cart-button",
            "[data-testid='fulfillment-summary']",
            ".fulfillment-fulfillment-summary",
            "[class*='fulfillment']",
            ".add-to-cart-button",
        ]:
            for el in soup.select(sel):
                avail_texts.append(el.get_text(separator=" ", strip=True))

        state.raw_availability_text = " | ".join(avail_texts)
        full_text = state.raw_availability_text.lower()

        # --- Detect NEW availability ---
        # Add to Cart button (non-open-box)
        add_to_cart_btn = soup.select_one(
            "button.add-to-cart-button:not([class*='open-box']), "
            "[data-testid='add-to-cart-button'], "
            "button[data-button-state='ADD_TO_CART']"
        )

        # Check button state
        btn_disabled = True
        if add_to_cart_btn:
            btn_disabled = (
                add_to_cart_btn.get("disabled") is not None
                or "disabled" in add_to_cart_btn.get("class", [])
                or add_to_cart_btn.get("data-button-state") in ("SOLD_OUT", "UNAVAILABLE", "COMING_SOON")
            )

        # JSON-LD / embedded data
        json_availability = ""
        for script in soup.select('script[type="application/ld+json"]'):
            try:
                ld = json.loads(script.string or "")
                if isinstance(ld, dict) and "offers" in ld:
                    offers = ld["offers"]
                    if isinstance(offers, dict):
                        json_availability = offers.get("availability", "")
                    elif isinstance(offers, list):
                        for o in offers:
                            json_availability = o.get("availability", "")
                            if "InStock" in json_availability:
                                break
            except (json.JSONDecodeError, TypeError):
                continue

        # Embedded JS state data
        button_state_match = re.search(r'"buttonState"\s*:\s*"([A-Z_]+)"', html)
        button_state = button_state_match.group(1) if button_state_match else ""

        # Determine NEW status
        if not btn_disabled and add_to_cart_btn:
            btn_text = add_to_cart_btn.get_text(strip=True).lower()
            if "add to cart" in btn_text:
                state.new_status = "add_to_cart"
        elif "InStock" in json_availability:
            state.new_status = "add_to_cart"
        elif button_state == "ADD_TO_CART":
            state.new_status = "add_to_cart"
        elif button_state in ("PRE_ORDER", "PREORDER"):
            state.new_status = "preorder"
        elif button_state in ("BACK_ORDER", "BACKORDER"):
            state.new_status = "backorder"

        # Refine: shipping vs pickup
        if state.new_status == "add_to_cart":
            if "ship" in full_text or "delivery" in full_text or "free shipping" in full_text:
                state.new_status = "available_ship"
            elif "pick up" in full_text or "pickup" in full_text or "store" in full_text:
                state.new_status = "available_pickup"
            # If both, prefer shipping as primary state
            if ("ship" in full_text or "delivery" in full_text) and ("pick up" in full_text or "pickup" in full_text):
                state.new_status = "available_ship"

        # Check sold out / coming soon signals
        if state.new_status == "unavailable":
            if "sold out" in full_text or button_state == "SOLD_OUT":
                state.new_status = "sold_out"
            elif "coming soon" in full_text or button_state == "COMING_SOON":
                state.new_status = "unavailable"

        # --- Detect open-box ---
        open_box_section = soup.select_one(
            "[class*='open-box'], "
            "[data-testid*='open-box'], "
            ".open-box-option, "
            "[class*='openBox']"
        )
        if open_box_section:
            state.open_box_status = "available"
            ob_price = open_box_section.select_one("[class*='price'] span, span[class*='Price']")
            if ob_price:
                state.open_box_price = ob_price.get_text(strip=True)

        # Also check text-level open-box mentions
        if "open-box" in full_text or "open box" in full_text:
            state.open_box_status = "available"

    # ---- Playwright fallback ----
    def check_playwright(self) -> ProductState:
        state = ProductState()
        state.url = self.url
        state.sku = self.sku
        state.source = "playwright"

        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(
                    user_agent=random.choice(USER_AGENTS),
                    viewport={"width": 1440, "height": 900},
                    locale="en-US",
                    timezone_id="America/New_York",
                )
                page = context.new_page()

                # Set zip code cookie
                context.add_cookies([{
                    "name": "intl_splash",
                    "value": "false",
                    "domain": ".bestbuy.com",
                    "path": "/",
                }, {
                    "name": "zipCode",
                    "value": ZIP_CODE,
                    "domain": ".bestbuy.com",
                    "path": "/",
                }])

                page.goto(self.url, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(3000)  # Let JS render

                html = page.content()

                if self._is_blocked(html):
                    state.error = "blocked-or-captcha-playwright"
                    log.warning("Playwright also blocked.")
                    browser.close()
                    return state

                soup = BeautifulSoup(html, "lxml")
                self._parse_product_page(soup, html, state)

                # Screenshot on every Playwright check
                SCREENSHOT_DIR.mkdir(exist_ok=True)
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                shot_path = SCREENSHOT_DIR / f"bestbuy_{ts}.png"
                page.screenshot(path=str(shot_path), full_page=True)
                log.info(f"Screenshot saved: {shot_path}")

                browser.close()

        except ImportError:
            state.error = "playwright-not-installed"
            log.error("Playwright not installed. Run: pip install playwright && playwright install chromium")
        except Exception as e:
            state.error = f"playwright-error: {e}"
            log.error(f"Playwright check failed: {e}")

        return state

    # ---- Combined check with auto-fallback ----
    def check(self) -> ProductState:
        state = self.check_static()

        # Fallback to Playwright if static was blocked or errored
        if state.error and "blocked" in (state.error or ""):
            log.info("Falling back to Playwright...")
            state = self.check_playwright()

        return state


# ---------------------------------------------------------------------------
# Alert logic
# ---------------------------------------------------------------------------


def should_alert(prev: dict, current: ProductState) -> bool:
    """Return True only when NEW inventory becomes available or price changes materially."""
    prev_new = prev.get("new_status", "unavailable")
    curr_new = current.new_status

    # NEW became available
    if not _is_new_available(prev_new) and current.new_available:
        return True

    # NEW price changed materially (>$5 difference)
    if current.new_available:
        prev_price = _parse_price(prev.get("price", ""))
        curr_price = _parse_price(current.price)
        if prev_price and curr_price and abs(prev_price - curr_price) > 5:
            return True

    return False


def _is_new_available(status: str) -> bool:
    return status in ("available_ship", "available_pickup", "add_to_cart")


def _parse_price(price_str: str) -> Optional[float]:
    match = re.search(r"[\d,]+\.?\d*", price_str.replace(",", ""))
    if match:
        try:
            return float(match.group())
        except ValueError:
            return None
    return None


def state_change_summary(prev: dict, current: ProductState) -> str:
    prev_new = prev.get("new_status", "unavailable")
    return f"{prev_new} -> {current.new_status}"


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------


def run_once(checker: BestBuyChecker, notifier: Notifier, store: StateStore):
    """Run a single check cycle."""
    state = checker.check()

    if state.error and "blocked" in (state.error or ""):
        log.warning(f"Check blocked: {state.error}. Will retry next cycle.")
        return

    log.info(state.summary_line())
    log_csv(state)

    prev = store.get("last_state", {})

    if should_alert(prev, state):
        summary = state_change_summary(prev, state)
        log.info(f"NEW INVENTORY DETECTED: {summary}")
        notifier.alert(
            product=state.product_name or PRODUCT_NAME,
            state_summary=summary,
            price=state.price,
            url=state.url,
        )
    elif state.open_box_status == "available" and prev.get("open_box_status") != "available":
        log.info(f"Open-box became available (price: {state.open_box_price}). NO ALERT — open-box only.")
    elif not state.new_available and state.open_box_status == "available":
        log.info(f"Open-box still available (price: {state.open_box_price}). No NEW inventory. No alert.")

    store.update({"last_state": state.to_dict()})


def run_loop(checker: BestBuyChecker, notifier: Notifier, store: StateStore):
    """Run continuously with jitter and backoff."""
    log.info(f"Starting stock checker loop. Interval: {CHECK_INTERVAL}s + jitter {JITTER_MIN}-{JITTER_MAX}s")
    log.info(f"Monitoring: {PRODUCT_NAME}")
    log.info(f"URL: {checker.url}")
    log.info(f"SKU: {checker.sku}")
    log.info(f"ZIP: {ZIP_CODE} | Store: {STORE_NAME}")

    consecutive_errors = 0
    MAX_BACKOFF = 1800  # 30 minutes max

    while True:
        try:
            run_once(checker, notifier, store)
            consecutive_errors = 0
        except Exception as e:
            consecutive_errors += 1
            backoff = min(CHECK_INTERVAL * (2 ** consecutive_errors), MAX_BACKOFF)
            log.error(f"Unhandled error (attempt {consecutive_errors}): {e}. Backing off {backoff}s.")
            time.sleep(backoff)
            continue

        jitter = random.randint(JITTER_MIN, JITTER_MAX)
        wait = CHECK_INTERVAL + jitter
        log.info(f"Next check in {wait}s ({CHECK_INTERVAL}s + {jitter}s jitter)")
        time.sleep(wait)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="Best Buy Stock Checker")
    parser.add_argument("--once", action="store_true", help="Run a single check and exit")
    parser.add_argument("--loop", action="store_true", help="Run continuously (default)")
    parser.add_argument("--test-notify", action="store_true", help="Send test notifications and exit")
    parser.add_argument("--url", default=DEFAULT_URL, help="Product URL to monitor")
    parser.add_argument("--sku", default=DEFAULT_SKU, help="Product SKU")
    parser.add_argument("--playwright", action="store_true", help="Force Playwright instead of static HTML")
    args = parser.parse_args()

    notifier = Notifier()

    if args.test_notify:
        notifier.test()
        return

    checker = BestBuyChecker(url=args.url, sku=args.sku)
    store = StateStore()

    if args.once:
        run_once(checker, notifier, store)
    else:
        run_loop(checker, notifier, store)


if __name__ == "__main__":
    main()
