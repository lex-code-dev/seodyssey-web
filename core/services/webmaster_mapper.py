from __future__ import annotations

from typing import Any


WEBMASTER_RULES = [
    {
        "issue_code": "webmaster_ssl_invalid",
        "severity": "fail",
        "title": "Некорректная настройка SSL-сертификата",
        "category": "security_ssl",
        "match_any": [
            "ssl-сертификат",
            "ssl сертификат",
            "https",
            "сертификат",
        ],
        "match_all": [
            ["ssl", "сертификат"],
        ],
    },
    {
        "issue_code": "webmaster_http_4xx_pages",
        "severity": "fail",
        "title": "Часть страниц отвечает кодом 4XX",
        "category": "http",
        "match_any": [
            "кодом 4xx",
            "код 4xx",
            "4xx",
        ],
        "match_all": [
            ["страниц", "4xx"],
            ["страница", "4xx"],
        ],
    },
    {
        "issue_code": "webmaster_missing_description",
        "severity": "warn",
        "title": "На части страниц отсутствует meta description",
        "category": "meta",
        "match_any": [
            "description",
            "meta description",
            "метатеги <description>",
            "метатег description",
        ],
        "match_all": [
            ["отсутств", "description"],
        ],
    },
    {
        "issue_code": "webmaster_duplicate_title",
        "severity": "warn",
        "title": "Обнаружены одинаковые title у разных страниц",
        "category": "meta",
        "match_any": [
            "одинаковые title",
            "одинаковый title",
            "duplicate title",
        ],
        "match_all": [
            ["title", "одинаков"],
        ],
    },
    {
        "issue_code": "webmaster_duplicate_description",
        "severity": "warn",
        "title": "Обнаружены одинаковые meta description",
        "category": "meta",
        "match_any": [
            "одинаковые meta description",
            "одинаковые description",
            "duplicate description",
        ],
        "match_all": [
            ["description", "одинаков"],
        ],
    },
    {
        "issue_code": "webmaster_broken_internal_links",
        "severity": "warn",
        "title": "Обнаружены неработающие ссылки",
        "category": "links",
        "match_any": [
            "неработающие ссылки",
            "битые ссылки",
            "broken links",
        ],
        "match_all": [
            ["ссылк", "неработ"],
        ],
    },
    {
        "issue_code": "webmaster_mobile_usability_issue",
        "severity": "warn",
        "title": "Есть проблемы с оптимизацией для мобильных устройств",
        "category": "mobile",
        "match_any": [
            "мобильных устройств",
            "мобильная версия",
            "mobile",
        ],
        "match_all": [
            ["мобиль", "проблем"],
        ],
    },
    {
        "issue_code": "webmaster_indexing_problem",
        "severity": "fail",
        "title": "Есть проблемы с индексированием страниц",
        "category": "indexing",
        "match_any": [
            "индексац",
            "не индекс",
            "проблемы с индексированием",
        ],
        "match_all": [
            ["индексац", "страниц"],
        ],
    },
    {
        "issue_code": "webmaster_security_or_malware_issue",
        "severity": "fail",
        "title": "Обнаружены проблемы безопасности или вредоносный код",
        "category": "security",
        "match_any": [
            "вредонос",
            "malware",
            "безопасност",
            "взлом",
        ],
        "match_all": [
            ["проблем", "безопасност"],
        ],
    },
    {
        "issue_code": "webmaster_spam_or_policy_violation",
        "severity": "fail",
        "title": "Обнаружены нарушения качества или поисковый спам",
        "category": "quality",
        "match_any": [
            "поисковый спам",
            "спам",
            "нарушени",
            "санкц",
        ],
        "match_all": [
            ["наруш", "качества"],
        ],
    },
    {
        "issue_code": "webmaster_duplicate_h1",
        "severity": "warn",
        "title": "Обнаружены одинаковые H1 у разных страниц",
        "category": "headings",
        "match_any": [
            "одинаковые h1",
            "duplicate h1",
        ],
        "match_all": [
            ["h1", "одинаков"],
        ],
    },
    {
        "issue_code": "webmaster_redirect_issue",
        "severity": "warn",
        "title": "Обнаружены проблемы с редиректами",
        "category": "redirects",
        "match_any": [
            "редирект",
            "redirect",
        ],
        "match_all": [
            ["проблем", "редирект"],
        ],
    },
    {
        "issue_code": "webmaster_robots_blocking",
        "severity": "fail",
        "title": "Важные страницы закрыты в robots.txt",
        "category": "robots",
        "match_any": [
            "robots.txt",
        ],
        "match_all": [
            ["robots", "закрыт"],
        ],
    },
    {
        "issue_code": "webmaster_noindex_issue",
        "severity": "fail",
        "title": "Важные страницы закрыты через noindex",
        "category": "robots",
        "match_any": [
            "noindex",
            "meta robots",
            "x-robots-tag",
        ],
        "match_all": [
            ["noindex", "страниц"],
        ],
    },
    {
        "issue_code": "webmaster_canonical_issue",
        "severity": "warn",
        "title": "Обнаружены проблемы с canonical",
        "category": "canonical",
        "match_any": [
            "canonical",
        ],
        "match_all": [
            ["canonical", "проблем"],
        ],
    },
    {
        "issue_code": "webmaster_sitemap_issue",
        "severity": "warn",
        "title": "Есть проблемы с sitemap.xml",
        "category": "sitemap",
        "match_any": [
            "sitemap.xml",
            "sitemap",
            "карта сайта",
        ],
        "match_all": [
            ["sitemap", "проблем"],
        ],
    },
    {
        "issue_code": "webmaster_structured_data_issue",
        "severity": "warn",
        "title": "Обнаружены проблемы со структурированными данными",
        "category": "structured_data",
        "match_any": [
            "структурирован",
            "микроразмет",
            "schema.org",
            "json-ld",
        ],
        "match_all": [
            ["разметк", "ошиб"],
        ],
    },
    {
        "issue_code": "webmaster_region_issue",
        "severity": "warn",
        "title": "Есть проблемы с региональностью сайта",
        "category": "region",
        "match_any": [
            "региональн",
            "регион сайта",
        ],
        "match_all": [
            ["регион", "сайт"],
        ],
    },
    {
        "issue_code": "webmaster_organization_profile_incomplete",
        "severity": "warn",
        "title": "Профиль организации заполнен не полностью",
        "category": "organization",
        "match_any": [
            "профиль организации",
            "карточк",
            "организац",
        ],
        "match_all": [
            ["организац", "профил"],
        ],
    },
    {
        "issue_code": "webmaster_chat_not_connected",
        "severity": "warn",
        "title": "Не подключён чат с организацией",
        "category": "organization",
        "match_any": [
            "чат с организацией",
            "чат",
        ],
        "match_all": [
            ["чат", "организац"],
        ],
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


def map_webmaster_issue(raw_issue: dict[str, Any]) -> dict[str, Any]:
    """
    Нормализует сырую проблему из Яндекс.Вебмастера в формат SEOdyssey.

    Ожидает словарь с чем-то вроде:
    {
        "title": "...",
        "description": "...",
        "status": "ERROR" / "WARNING" / ...
        ...
    }
    """
    external_title = _safe_text(raw_issue.get("title") or raw_issue.get("name"))
    external_description = _safe_text(
        raw_issue.get("description") or raw_issue.get("message") or raw_issue.get("details")
    )
    external_status = _safe_text(raw_issue.get("status")).upper()

    haystack = _normalize_text(external_title, external_description)

    matched_rule = None
    for rule in WEBMASTER_RULES:
        if _matches_rule(haystack, rule):
            matched_rule = rule
            break

    if matched_rule:
        issue_code = matched_rule["issue_code"]
        severity = matched_rule["severity"]
        title = matched_rule["title"]
        category = matched_rule.get("category", "other")
    else:
        issue_code = "webmaster_unknown_issue"
        severity = "fail" if external_status in {"ERROR", "FAIL", "CRITICAL"} else "warn"
        title = external_title or "Неизвестная проблема из Яндекс.Вебмастера"
        category = "unknown"

    if external_status in {"WARNING", "WARN", "RECOMMENDATION"} and issue_code != "webmaster_unknown_issue":
        severity = "warn"
    elif external_status in {"ERROR", "FAIL", "CRITICAL"} and issue_code != "webmaster_unknown_issue":
        severity = "fail"

    details = {
        "source": "yandex_webmaster",
        "issue_code": issue_code,
        "external_title": external_title,
        "external_description": external_description,
        "external_status": external_status,
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