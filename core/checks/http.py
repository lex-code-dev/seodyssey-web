import time
from typing import Any, Dict, List, Optional

import requests
from requests.exceptions import RequestException

from .types import CheckItem

HTTP_TIMEOUT_SEC = 10
HTTP_ATTEMPTS = 3
HTTP_REQUIRED_SUCCESSES = 2
HTTP_SLEEP_BETWEEN_ATTEMPTS = 1.0

# Статусы "сайт отвечает, но доступ ограничен" — это не даунтайм.
HTTP_BLOCKED_STATUSES = {401, 403, 429}


class HttpCheck:
    name = "http"

    # Практика показала, что некоторые сайты (WAF/CDN) режут python-requests.
    # "curl-like" заголовки обычно проходят стабильнее.
    headers = {
        "User-Agent": "curl/8.0",
        "Accept": "*/*",
        "Connection": "close",
    }

    def run(self, *, url: str) -> CheckItem:
        attempts: List[Dict[str, Any]] = []
        success_count = 0
        last_status: Optional[int] = None
        last_error: Optional[str] = None

        for i in range(HTTP_ATTEMPTS):
            try:
                r = requests.get(
                    url,
                    timeout=HTTP_TIMEOUT_SEC,
                    allow_redirects=True,
                    headers=self.headers,
                )
                code = r.status_code
                last_status = code

                ok = 200 <= code < 400
                attempts.append({"ok": ok, "http_status": code})
                if ok:
                    success_count += 1

            except RequestException as exc:
                last_error = str(exc)
                attempts.append({"ok": False, "error": last_error})

            if i < HTTP_ATTEMPTS - 1:
                time.sleep(HTTP_SLEEP_BETWEEN_ATTEMPTS)

        # 1) OK: достаточно успешных ответов (как было)
        if success_count >= HTTP_REQUIRED_SUCCESSES:
            return CheckItem(
                status="ok",
                details={
                    "url": url,
                    "is_up": True,
                    "successes": success_count,
                    "attempts": attempts,
                    "last_http_status": last_status,
                    "summary": {
                        "title": "HTTP: сайт доступен",
                        "lines": [f"Последний статус: {last_status}"] if last_status else [],
                    },
                },
            )

        # 2) WARN: сайт отвечает, но ограничивает доступ (не считаем "недоступен")
        if last_status in HTTP_BLOCKED_STATUSES:
            return CheckItem(
                status="warn",
                details={
                    "url": url,
                    "is_up": True,
                    "successes": success_count,
                    "attempts": attempts,
                    "last_http_status": last_status,
                    "last_error": last_error,
                    "summary": {
                        "title": f"HTTP: доступ ограничен (status {last_status})",
                        "lines": [
                            "Сайт отвечает, но режет запросы роботов/скриптов (WAF/CDN).",
                            "Это не даунтайм. Для проверки доступности можно менять профиль запроса.",
                        ],
                    },
                },
            )

        # 3) FAIL: реально плохо (таймаут/5xx/сеть/и т.п.)
        return CheckItem(
            status="fail",
            details={
                "url": url,
                "is_up": False,
                "successes": success_count,
                "attempts": attempts,
                "last_http_status": last_status,
                "last_error": last_error,
                "summary": {
                    "title": "HTTP: сайт недоступен",
                    "lines": [
                        f"Последний статус: {last_status}"
                        if last_status
                        else "Нет HTTP-статуса (ошибка соединения)",
                        f"Ошибка: {last_error}" if last_error else "Ошибка: —",
                    ],
                },
            },
        )
