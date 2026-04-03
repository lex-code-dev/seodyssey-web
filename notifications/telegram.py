import requests
from django.conf import settings


def send_telegram_message(chat_id, text):
    token = settings.TELEGRAM_BOT_TOKEN
    if not token:
        print("Нет TELEGRAM_BOT_TOKEN")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"

    data = {
        "chat_id": chat_id,
        "text": text,
    }

    try:
        response = requests.post(url, json=data, timeout=5)
        print("Статус Telegram:", response.status_code)
        print(response.text)
    except Exception as e:
        print("Ошибка:", e)
        return False

    return True
