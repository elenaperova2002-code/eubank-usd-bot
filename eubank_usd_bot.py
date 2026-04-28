"""
Бот, который отслеживает курс USD во вкладке "В Smartbank" на сайте
eubank.kz/exchange-rates и присылает обновления в Telegram при изменении.

Запускается из GitHub Actions по расписанию.
Токены берутся из переменных окружения TELEGRAM_TOKEN и TELEGRAM_CHAT_ID
(они задаются в Settings → Secrets репозитория).
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
    """
    Открывает страницу через Chromium, переключается на вкладку 'В Smartbank'
    и возвращает {'buy': 457.5, 'sell': 460.5}.
    """
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
        page.goto(URL, wait_until="networkidle", timeout=60_000)

        # Переключаемся на вкладку «В Smartbank»
        try:
            page.get_by_text("В Smartbank", exact=True).first.click(timeout=5_000)
            page.wait_for_timeout(1500)
        except Exception:
            pass  # вкладка уже могла быть активна

        # Берём строку с USD и достаём из неё две цифры (покупка/продажа)
        usd_row = page.locator("text=USD").first
        usd_row.wait_for(timeout=10_000)
        row_text = usd_row.evaluate(
            "el => { let n = el; for (let i=0;i<6;i++){ if(!n.parentElement) break; "
            "n = n.parentElement; if (n.innerText && n.innerText.match(/\\d+[.,]\\d+/g)?.length>=2) return n.innerText; } "
            "return n.innerText; }"
        )
        browser.close()

    numbers = re.findall(r"\d+[.,]\d+", row_text)
    if len(numbers) < 2:
        raise RuntimeError(f"Не удалось распарсить курс. Текст строки: {row_text!r}")

    buy = float(numbers[0].replace(",", "."))
    sell = float(numbers[1].replace(",", "."))
    return {"buy": buy, "sell": sell}


def load_last_rate() -> dict | None:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def save_rate(rate: dict) -> None:
    STATE_FILE.write_text(
        json.dumps(rate, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def send_telegram(text: str) -> None:
    api = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    resp = requests.post(
        api,
        json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=15,
    )
    resp.raise_for_status()


def format_message(current: dict, previous: dict | None) -> str:
    arrow_buy = arrow_sell = ""
    if previous:
        if current["buy"] > previous["buy"]:
            arrow_buy = " 📈"
        elif current["buy"] < previous["buy"]:
            arrow_buy = " 📉"
        if current["sell"] > previous["sell"]:
            arrow_sell = " 📈"
        elif current["sell"] < previous["sell"]:
            arrow_sell = " 📉"

    lines = [
        "<b>💵 Eurasian Bank · Smartbank · USD</b>",
        f"Покупка: <b>{current['buy']} ₸</b>{arrow_buy}",
        f"Продажа: <b>{current['sell']} ₸</b>{arrow_sell}",
    ]
    if previous:
        lines.append("")
        lines.append(
            f"<i>Было: покупка {previous['buy']} ₸ / продажа {previous['sell']} ₸</i>"
        )
    lines.append('<a href="https://eubank.kz/exchange-rates">Открыть на сайте</a>')
    return "\n".join(lines)


def main() -> int:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("[ERROR] Не заданы TELEGRAM_TOKEN или TELEGRAM_CHAT_ID", file=sys.stderr)
        return 1

    try:
        current = fetch_usd_rate()
    except Exception as e:
        print(f"[ERROR] Не удалось получить курс: {e}", file=sys.stderr)
        return 1

    previous = load_last_rate()
    changed = (
        previous is None
        or previous.get("buy") != current["buy"]
        or previous.get("sell") != current["sell"]
    )

    if changed or FORCE:
        send_telegram(format_message(current, previous))
        save_rate(current)
        print(f"[OK] Отправлено: {current}")
    else:
        print(f"[OK] Без изменений: {current}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
