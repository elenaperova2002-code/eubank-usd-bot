"""
Бот, который отслеживает курс USD во вкладке "В Smartbank" на сайте
eubank.kz/exchange-rates и присылает обновления в Telegram при изменении.
"""

import json
import os
import re
import sys
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

URL = "https://eubank.kz/exchange-rates"
STATE_FILE = Path(__file__).parent / "last_rate.json"

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
FORCE = os.environ.get("FORCE", "").lower() in ("1", "true", "yes")


def fetch_usd_rate() -> dict:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0 Safari/537.36"
            )
        )
        page = context.new_page()
        page.goto(URL, wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_selector("text=USD", timeout=30_000)

        try:
            page.get_by_text("В Smartbank", exact=True).first.click(timeout=5_000)
            page.wait_for_timeout(2000)
        except Exception:
            pass

        page.wait_for_selector("text=USD", timeout=10_000)
        full_text = page.evaluate("() => document.body.innerText")
        browser.close()

    match = re.search(r"USD[\s\S]*?(?=\b[A-Z]{3}\b)", full_text)
    if not match:
        match = re.search(r"USD[^\n]*", full_text)
    if not match:
        raise RuntimeError(f"Не нашли USD на странице. Начало текста: {full_text[:500]!r}")

    usd_block = match.group(0)
    numbers = re.findall(r"\d+(?:[.,]\d+)?", usd_block)
    rate_numbers = [float(n.replace(",", ".")) for n in numbers]
    rate_numbers = [n for n in rate_numbers if n >= 100]

    if len(rate_numbers) < 2:
        raise RuntimeError(f"Не удалось распарсить курс USD. Блок: {usd_block!r}")

    buy = rate_numbers[0]
    sell = rate_numbers[1]

    if not (100 <= buy <= 1000 and 100 <= sell <= 1000):
        raise RuntimeError(f"Подозрительный курс: {buy}/{sell}. Блок: {usd_block!r}")

    if sell < buy:
        raise RuntimeError(f"Курс продажи меньше покупки: {buy}/{sell}. Блок: {usd_block!r}")

    return {"buy": buy, "sell": sell}


def load_last_rate():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def save_rate(rate):
    STATE_FILE.write_text(json.dumps(rate, ensure_ascii=False, indent=2), encoding="utf-8")


def send_telegram(text):
    api = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    resp = requests.post(api, json={
        "chat_id": TELEGRAM_CHAT_ID, "text": text,
        "parse_mode": "HTML", "disable_web_page_preview": True,
    }, timeout=15)
    resp.raise_for_status()


def format_message(current, previous):
    arrow_buy = arrow_sell = ""
    if previous:
        if current["buy"] > previous["buy"]: arrow_buy = " 📈"
        elif current["buy"] < previous["buy"]: arrow_buy = " 📉"
        if current["sell"] > previous["sell"]: arrow_sell = " 📈"
        elif current["sell"] < previous["sell"]: arrow_sell = " 📉"

    lines = [
        "<b>💵 Eurasian Bank · Smartbank · USD</b>",
        f"Покупка: <b>{current['buy']} ₸</b>{arrow_buy}",
        f"Продажа: <b>{current['sell']} ₸</b>{arrow_sell}",
    ]
    if previous:
        lines.append("")
        lines.append(f"<i>Было: покупка {previous['buy']} ₸ / продажа {previous['sell']} ₸</i>")
    lines.append('<a href="https://eubank.kz/exchange-rates">Открыть на сайте</a>')
    return "\n".join(lines)


def main():
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("[ERROR] Не заданы TELEGRAM_TOKEN или TELEGRAM_CHAT_ID", file=sys.stderr)
        return 1
    try:
        current = fetch_usd_rate()
    except Exception as e:
        print(f"[ERROR] Не удалось получить курс: {e}", file=sys.stderr)
        return 1

    previous = load_last_rate()
    changed = (previous is None or previous.get("buy") != current["buy"] or previous.get("sell") != current["sell"])

    if changed or FORCE:
        send_telegram(format_message(current, previous))
        save_rate(current)
        print(f"[OK] Отправлено: {current}")
    else:
        print(f"[OK] Без изменений: {current}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
