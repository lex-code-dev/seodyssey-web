from datetime import datetime, timezone as dt_timezone
from typing import Optional, Tuple

import whois

from .types import CheckItem

DOMAIN_WARN_DAYS = 30


class WhoisXmlDomainCheck:
    name = "domain"

    def run(self, *, domain: str) -> CheckItem:
        days_left, err, expires_raw = self._get_domain_expiry_days(domain)

        if err:
            return CheckItem(status="unknown", details={"error": err, "expires_raw": expires_raw, "summary": {"title": "Домен: не удалось получить дату окончания", "lines": [f"Ошибка: {err}"]}})

        if days_left is None:
            return CheckItem(status="unknown", details={"error": "days_left_none", "expires_raw": expires_raw, "summary": {"title": "Домен: не удалось получить дату окончания", "lines": ["Ошибка: days_left_none"]}})

        if days_left < 0:
            return CheckItem(status="fail", details={"expires_in_days": days_left, "expires_raw": expires_raw, "summary": {"title": "Домен: истёк", "lines": [f"Дней до окончания: {days_left}", f"expires_raw: {expires_raw}"]}})

        if days_left <= DOMAIN_WARN_DAYS:
            return CheckItem(status="warning", details={"expires_in_days": days_left, "warn_days": DOMAIN_WARN_DAYS, "expires_raw": expires_raw, "summary": {"title": "Домен: скоро истечёт", "lines": [f"Осталось дней: {days_left} (порог {DOMAIN_WARN_DAYS})"]}})

        return CheckItem(status="ok", details={"expires_in_days": days_left, "warn_days": DOMAIN_WARN_DAYS, "expires_raw": expires_raw, "summary": {"title": "Домен: всё ок", "lines": [f"Осталось дней: {days_left}"]}})

    def _get_domain_expiry_days(self, domain: str) -> Tuple[Optional[int], Optional[str], Optional[str]]:
        try:
            w = whois.whois(domain)
            exp = w.expiration_date

            # expiration_date может быть списком
            if isinstance(exp, list):
                exp = exp[0]

            if exp is None:
                return None, "expiresDate_not_found", None

            expires_raw = str(exp)

            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=dt_timezone.utc)

            now = datetime.now(dt_timezone.utc)
            days_left = int((exp - now).total_seconds() // 86400)
            return days_left, None, expires_raw

        except Exception as exc:
            return None, str(exc), None