from __future__ import annotations

from typing import Any


CODE_RULES = {
    "SSL_CERTIFICATE_ERROR": {
        "issue_code": "webmaster_ssl_invalid",
        "severity": "fail",
        "title": "Некорректная настройка SSL-сертификата",
        "category": "security_ssl",
    },
    "URL_ALERT_4XX": {
        "issue_code": "webmaster_http_4xx_pages",
        "severity": "fail",
        "title": "Часть страниц отвечает кодом 4XX",
        "category": "http",
    },
    "DOCUMENTS_MISSING_DESCRIPTION": {
        "issue_code": "webmaster_missing_description",
        "severity": "warn",
        "title": "На части страниц отсутствует meta description",
        "category": "meta",
    },
    "DOCUMENTS_MISSING_TITLE": {
        "issue_code": "webmaster_duplicate_title",
        "severity": "warn",
        "title": "Есть проблемы с title страниц",
        "category": "meta",
    },
    "ERRORS_IN_SITEMAPS": {
        "issue_code": "webmaster_sitemap_issue",
        "severity": "warn",
        "title": "Есть проблемы с sitemap.xml",
        "category": "sitemap",
    },
    "NO_REGIONS": {
        "issue_code": "webmaster_region_issue",
        "severity": "warn",
        "title": "Есть проблемы с региональностью сайта",
        "category": "region",
    },
    "NOT_MOBILE_FRIENDLY": {
        "issue_code": "webmaster_mobile_usability_issue",
        "severity": "warn",
        "title": "Есть проблемы с оптимизацией для мобильных устройств",
        "category": "mobile",
    },
    "ROBOTS_TXT_PROHIBITS_INDEXING": {
        "issue_code": "webmaster_robots_blocking",
        "severity": "fail",
        "title": "Важные страницы закрыты в robots.txt",
        "category": "robots",
    },
    "NO_ROBOTS_TXT": {
        "issue_code": "webmaster_robots_blocking",
        "severity": "warn",
        "title": "Не найден robots.txt",
        "category": "robots",
    },
}


WEBMASTER_RULES = [
    {
        "issue_code": "webmaster_ssl_invalid",
        "severity": "fail",
        "title": "Некорректная настройка SSL-сертификата",
        "category": "security_ssl",
        "match_any": ["ssl-сертификат", "ssl сертификат", "https", "сертификат"],
        "match_all": [["ssl", "сертификат"]],
    },
    {
        "issue_code": "webmaster_http_4xx_pages",
        "severity": "fail",
        "title": "Часть страниц отвечает кодом 4XX",
        "category": "http",
        "match_any": ["кодом 4xx", "код 4xx", "4xx"],
        "match_all": [["страниц", "4xx"], ["страница", "4xx"]],
    },
    {
        "issue_code": "webmaster_missing_description",
        "severity": "warn",
        "title": "На части страниц отсутствует meta description",
        "category": "meta",
        "match_any": ["description", "meta description", "метатеги <description>", "метатег description"],
        "match_all": [["отсутств", "description"]],
    },
    {
        "issue_code": "webmaster_duplicate_title",
        "severity": "warn",
        "title": "Есть проблемы с title страниц",
        "category": "meta",
        "match_any": ["одинаковые title", "одинаковый title", "duplicate title", "missing title", "title"],
        "match_all": [["title", "одинаков"]],
    },
    {
        "issue_code": "webmaster_sitemap_issue",
        "severity": "warn",
        "title": "Есть проблемы с sitemap.xml",
        "category": "sitemap",
        "match_any": ["sitemap.xml", "sitemap", "карта сайта"],
        "match_all": [["sitemap", "проблем"]],
    },
    {
        "issue_code": "webmaster_region_issue",
        "severity": "warn",
        "title": "Есть проблемы с региональностью сайта",
        "category": "region",
        "match_any": ["региональн", "регион сайта"],
        "match_all": [["регион", "сайт"]],
    },
]


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _normalize_text(*parts: Any) -> str:
    text = " ".join(_safe_text(p) for p in parts if _safe_text(p))
    return text.lower()


def _matches_rule(text: str, rule: dict[str, Any]) -> bool:
    match_any = rule.get("match_any") or []
    match_all = rule.get("match_all") or []

    if any(token.lower() in text for token in match_any):
        return True

    for group in match_all:
        if all(token.lower() in text for token in group):
            return True

    return False


def _severity_from_external(external_severity: str, default: str) -> str:
    sev = _safe_text(external_severity).upper()

    if sev in {"FATAL", "CRITICAL"}:
        return "fail"

    if sev in {"POSSIBLE_PROBLEM", "RECOMMENDATION", "WARNING", "WARN"}:
        return "warn"

    return default


def map_webmaster_issue(raw_issue: dict[str, Any]) -> dict[str, Any]:
    """
    Нормализует сырую проблему из Яндекс.Вебмастера в формат SEOdyssey.
    """
    external_code = _safe_text(raw_issue.get("code")).upper()
    external_title = _safe_text(raw_issue.get("title") or raw_issue.get("name") or external_code)
    external_description = _safe_text(
        raw_issue.get("description") or raw_issue.get("message") or raw_issue.get("details")
    )
    external_status = _safe_text(raw_issue.get("status")).upper()
    external_severity = _safe_text(raw_issue.get("severity")).upper()
    external_state = _safe_text(raw_issue.get("state")).upper()

    matched_rule = CODE_RULES.get(external_code)

    if matched_rule:
        issue_code = matched_rule["issue_code"]
        severity = _severity_from_external(external_severity, matched_rule["severity"])
        title = matched_rule["title"]
        category = matched_rule.get("category", "other")
    else:
        haystack = _normalize_text(external_title, external_description, external_code)
        text_rule = None
        for rule in WEBMASTER_RULES:
            if _matches_rule(haystack, rule):
                text_rule = rule
                break

        if text_rule:
            issue_code = text_rule["issue_code"]
            severity = _severity_from_external(external_severity, text_rule["severity"])
            title = text_rule["title"]
            category = text_rule.get("category", "other")
        else:
            issue_code = "webmaster_unknown_issue"
            severity = _severity_from_external(external_severity, "warn")
            title = external_title or external_code or "Неизвестная проблема из Яндекс.Вебмастера"
            category = "unknown"

    details = {
        "source": "yandex_webmaster",
        "issue_code": issue_code,
        "external_code": external_code,
        "external_title": external_title,
        "external_description": external_description,
        "external_status": external_status,
        "external_severity": external_severity,
        "external_state": external_state,
        "category": category,
        "raw_payload": raw_issue,
    }

    return {
        "source": "yandex_webmaster",
        "check_key": "webmaster",
        "issue_code": issue_code,
        "severity": severity,
        "title": title,
        "details": details,
    }