import time
import requests
from django.conf import settings


def send_telegram_message(chat_id: str, text: str) -> dict:
    token = getattr(settings, "TELEGRAM_BOT_TOKEN", "")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured")

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
    }

    last_error = None

    for attempt in range(1, 4):  # 3 попытки
        try:
            response = requests.post(url, json=payload, timeout=20)
            response.raise_for_status()

            data = response.json()
            if not data.get("ok"):
                raise RuntimeError(f"Telegram API error: {data}")

            return data

        except Exception as exc:
            last_error = exc
            print(f"[TELEGRAM][attempt {attempt}/3] chat_id={chat_id} error={exc}")

            if attempt < 3:
                time.sleep(3)

    raise last_error