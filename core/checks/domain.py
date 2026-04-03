from datetime import datetime, timezone as dt_timezone
from typing import Any, Dict, Optional, Tuple

import requests
from django.conf import settings

from .types import CheckItem

DOMAIN_WARN_DAYS = 30


class WhoisXmlDomainCheck:
    name = "domain"

    def run(self, *, domain: str) -> CheckItem:
        api_key = getattr(settings, "WHOISXML_API_KEY", "") or ""
        if not api_key:
            return CheckItem(status="skipped", details={"reason": "WHOISXML_API_KEY_not_set", "summary": {"title": "Домен: проверка пропущена", "lines": ["WHOISXML_API_KEY не задан"]}})

        days_left, err, expires_raw = self._whoisxml_domain_expiry_days(domain, api_key)
        if err:
            return CheckItem(status="unknown", details={"error": err, "expires_raw": expires_raw, "summary": {"title": "Домен: не удалось получить дату окончания", "lines": [f"Ошибка: {err}"]}})

        if days_left is None:
            return CheckItem(status="unknown", details={"error": "days_left_none", "expires_raw": expires_raw, "summary": {"title": "Домен: не удалось получить дату окончания", "lines": ["Ошибка: days_left_none"]}})

        if days_left < 0:
            return CheckItem(status="fail", details={"expires_in_days": days_left, "expires_raw": expires_raw, "summary": {"title": "Домен: истёк", "lines": [f"Дней до окончания: {days_left}", f"expires_raw: {expires_raw}"]}})

        if days_left <= DOMAIN_WARN_DAYS:
            return CheckItem(status="warning", details={"expires_in_days": days_left, "warn_days": DOMAIN_WARN_DAYS, "expires_raw": expires_raw, "summary": {"title": "Домен: скоро истечёт", "lines": [f"Осталось дней: {days_left} (порог {DOMAIN_WARN_DAYS})"]}})

        return CheckItem(status="ok", details={"expires_in_days": days_left, "warn_days": DOMAIN_WARN_DAYS, "expires_raw": expires_raw, "summary": {"title": "Домен: всё ок", "lines": [f"Осталось дней: {days_left}"]}})

    def _whoisxml_domain_expiry_days(self, domain: str, api_key: str) -> Tuple[Optional[int], Optional[str], Optional[str]]:
        try:
            endpoint = "https://www.whoisxmlapi.com/whoisserver/WhoisService"
            params = {"apiKey": api_key, "domainName": domain, "outputFormat": "JSON"}
            r = requests.get(endpoint, params=params, timeout=15)
            r.raise_for_status()
            data = r.json() or {}

            expires_raw = self._extract_expires_date(data)
            if not expires_raw:
                return None, "expiresDate_not_found", None

            expires_dt = self._parse_expires_dt(expires_raw)
            if not expires_dt:
                return None, "expiresDate_parse_failed", expires_raw

            now = datetime.now(dt_timezone.utc)
            days_left = int((expires_dt - now).total_seconds() // 86400)
            return days_left, None, expires_raw
        except Exception as exc:
            return None, str(exc), None

    def _extract_expires_date(self, data: Dict[str, Any]) -> Optional[str]:
        wr = (data or {}).get("WhoisRecord") or {}
        reg = wr.get("registryData") or {}
        return reg.get("expiresDate") or wr.get("expiresDate")

    def _parse_expires_dt(self, raw: str) -> Optional[datetime]:
        try:
            s = raw.strip()
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=dt_timezone.utc)
            return dt.astimezone(dt_timezone.utc)
        except Exception:
            return None