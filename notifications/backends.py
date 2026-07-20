"""E-mail бэкенд через HTTPS API Unisender Go.

Хостинг (Timeweb VPS) блокирует исходящие SMTP-порты (25/465/587/2525),
поэтому SMTP-бэкенд Django с прода не работает — письма отправляются
через transactional API Unisender Go по 443. API-ключ совпадает
с SMTP-паролем аккаунта (EMAIL_HOST_PASSWORD).
"""

from email.utils import parseaddr

import requests
from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend

API_URL = "https://go2.unisender.ru/ru/transactional/api/v1/email/send.json"
API_TIMEOUT_SEC = 20


class UnisenderGoApiError(Exception):
    pass


class UnisenderGoBackend(BaseEmailBackend):
    def send_messages(self, email_messages):
        sent = 0
        for message in email_messages:
            try:
                self._send(message)
                sent += 1
            except Exception:
                if not self.fail_silently:
                    raise
        return sent

    def _send(self, message):
        if not message.recipients():
            return

        from_name, from_email = parseaddr(message.from_email or settings.DEFAULT_FROM_EMAIL)

        body = {"plaintext": message.body or ""}
        for content, mimetype in getattr(message, "alternatives", None) or []:
            if mimetype == "text/html":
                body["html"] = content

        payload = {
            "message": {
                "recipients": [{"email": addr} for addr in message.recipients()],
                "subject": message.subject,
                "from_email": from_email,
                "from_name": from_name,
                "body": body,
            }
        }

        resp = requests.post(
            API_URL,
            json=payload,
            headers={"X-API-KEY": settings.UNISENDER_GO_API_KEY},
            timeout=API_TIMEOUT_SEC,
        )
        try:
            data = resp.json()
        except ValueError:
            data = {}
        if resp.status_code != 200 or data.get("status") != "success":
            raise UnisenderGoApiError(
                f"Unisender Go API error: http={resp.status_code} response={resp.text[:500]}"
            )
