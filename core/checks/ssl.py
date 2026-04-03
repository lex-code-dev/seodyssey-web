from datetime import datetime, timezone as dt_timezone
import socket
import ssl
import certifi
from typing import Optional, Tuple

from .types import CheckItem

HTTP_TIMEOUT_SEC = 10
SSL_WARN_DAYS = 14


class SslCheck:
    name = "ssl"

    def _ssl_expiry_days(self, domain: str, port: int = 443) -> Tuple[Optional[int], Optional[str]]:
        try:
            ctx = ssl.create_default_context(cafile=certifi.where())
            with socket.create_connection((domain, port), timeout=HTTP_TIMEOUT_SEC) as sock:
                with ctx.wrap_socket(sock, server_hostname=domain) as ssock:
                    cert = ssock.getpeercert()
            not_after = cert.get("notAfter")
            if not not_after:
                return None, "no_notAfter_in_cert"

            expires = datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=dt_timezone.utc)
            now = datetime.now(tz=dt_timezone.utc)
            days_left = int((expires - now).total_seconds() // 86400)
            return days_left, None
        except Exception as exc:
            return None, str(exc)

    def run(self, *, domain: str) -> CheckItem:
        days_left, err = self._ssl_expiry_days(domain)
        if err:
            err_lower = err.lower()
            if "unable to get local issuer certificate" in err_lower or "certificate verify failed" in err_lower:
                return CheckItem(status="warning", details={"error": err, "note": "local_ca_issue", "summary": {"title": "SSL: предупреждение", "lines": ["Проблема с проверкой цепочки сертификата (CA)."]}})
            return CheckItem(status="fail", details={"error": err, "summary": {"title": "SSL: проверка не прошла", "lines": [f"Ошибка: {err}"]}})

        assert days_left is not None
        if days_left < 0:
            return CheckItem(status="fail", details={"expires_in_days": days_left, "summary": {"title": "SSL: сертификат истёк", "lines": [f"Дней до окончания: {days_left}"]}})
        if days_left <= SSL_WARN_DAYS:
            return CheckItem(status="warning", details={"expires_in_days": days_left, "warn_days": SSL_WARN_DAYS, "summary": {"title": "SSL: скоро истечёт", "lines": [f"Осталось дней: {days_left} (порог {SSL_WARN_DAYS})"]}})

        return CheckItem(status="ok", details={"expires_in_days": days_left, "warn_days": SSL_WARN_DAYS, "summary": {"title": "SSL: всё ок", "lines": [f"Осталось дней: {days_left}"]}})