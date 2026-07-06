import time
import requests
from django.conf import settings


def _format_alert(site, site_url: str, alert_lines: list, overall_fail: bool) -> tuple:
    """
    Формирует HTML-сообщение и inline-кнопки для Telegram.
    Возвращает (text, reply_markup).
    """
    app_domain = getattr(settings, "APP_DOMAIN", "app.seodyssey.ru")
    site_link = f"https://{app_domain}/sites/{site.id}/"

    if overall_fail:
        header = "🚨 <b>Обнаружены проблемы</b>"
    else:
        header = "⚠️ <b>Есть предупреждения</b>"

    WEBMASTER_TITLES = {
        "BIG_FAVICON_ABSENT": "Отсутствует favicon высокого разрешения",
        "SSL_CERTIFICATE_ERROR": "Некорректная настройка SSL-сертификата",
        "NOT_MOBILE_FRIENDLY": "Проблемы с оптимизацией для мобильных",
        "DOCUMENTS_MISSING_DESCRIPTION": "На части страниц отсутствует meta description",
    }

    FAIL_KEYWORDS = ("недоступен", "упал", "критич", "ssl", "dns", "http", "не резолв")

    fails = []
    warns = []
    for line in alert_lines:
        clean = line.lstrip("• ").strip()
        clean = clean.removeprefix("Вебмастер: ")
        for code, human in WEBMASTER_TITLES.items():
            clean = clean.replace(code, human)
        if any(w in clean.lower() for w in FAIL_KEYWORDS):
            fails.append(f"❌ {clean}")
        else:
            warns.append(f"⚠️ {clean}")

    problems = "\n".join(fails + warns)

    # Считаем количество проблем для кнопки
    total = len(fails) + len(warns)
    problems_label = f"🔍 Открыть детали ({total})" if total > 0 else "🔍 Открыть детали"

    text = (
        f"{header}\n\n"
        f"<b>Сайт:</b> <a href=\"https://{site.domain}\">{site.domain}</a>\n\n"
        f"{problems}"
    )

    reply_markup = {
        "inline_keyboard": [
            [
                {"text": problems_label, "url": site_link},
            ]
        ]
    }

    return text, reply_markup


def _format_ok(site) -> tuple:
    app_domain = getattr(settings, "APP_DOMAIN", "app.seodyssey.ru")
    site_link = f"https://{app_domain}/sites/{site.id}/"

    text = (
        f"✅ <b>Всё в порядке</b>\n\n"
        f"<b>Сайт:</b> <a href=\"https://{site.domain}\">{site.domain}</a>\n\n"
        f"Последняя проверка прошла без проблем."
    )

    reply_markup = {
        "inline_keyboard": [
            [
                {"text": "📊 Открыть сайт", "url": site_link},
            ]
        ]
    }

    return text, reply_markup


def send_telegram_message(chat_id: str, text: str, reply_markup: dict = None) -> dict:
    token = getattr(settings, "TELEGRAM_BOT_TOKEN", "")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured")

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    if reply_markup:
        import json
        payload["reply_markup"] = json.dumps(reply_markup)
        payload["disable_web_page_preview"] = False  # чтобы кнопки работали корректно

    last_error = None

    for attempt in range(1, 4):
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