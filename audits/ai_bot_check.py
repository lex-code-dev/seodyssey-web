"""
Проверка robots.txt на блокировку AI-ботов, важных для GEO-видимости.

Чистый модуль без зависимостей от моделей проекта — можно тестировать отдельно.
Разбор правил — в audits.robots_rules: стандартный urllib.robotparser не
понимает ни `*` внутри пути, ни якорь `$`, и пропускал бы такие запреты.
"""

from urllib.parse import urlparse, urlunparse

import httpx

from audits.net_guard import safe_get
from audits.robots_rules import parse_robots

# Боты, критичные для попадания бренда в AI-ответы (RU + глобальные).
#   token    — как пишется в robots.txt
#   label    — человекочитаемо для отчёта
#   critical — блокировка напрямую выбивает из соответствующей AI-выдачи
AI_BOTS = [
    {"token": "OAI-SearchBot",   "label": "ChatGPT Search (OAI-SearchBot)",     "vendor": "OpenAI",       "critical": True},
    {"token": "ChatGPT-User",    "label": "ChatGPT Browsing (ChatGPT-User)",    "vendor": "OpenAI",       "critical": True},
    {"token": "GPTBot",          "label": "OpenAI Training (GPTBot)",           "vendor": "OpenAI",       "critical": False},
    {"token": "YandexBot",       "label": "Yandex (YandexBot)",                 "vendor": "Yandex",       "critical": True},
    {"token": "Google-Extended", "label": "Google Gemini/AIO (Google-Extended)","vendor": "Google",       "critical": True},
    {"token": "PerplexityBot",   "label": "Perplexity (PerplexityBot)",         "vendor": "Perplexity",   "critical": False},
    {"token": "Perplexity-User", "label": "Perplexity Browsing (Perplexity-User)", "vendor": "Perplexity","critical": False},
    {"token": "ClaudeBot",       "label": "Anthropic (ClaudeBot)",              "vendor": "Anthropic",    "critical": False},
    {"token": "CCBot",           "label": "Common Crawl (CCBot)",               "vendor": "Common Crawl", "critical": False},
]

DEFAULT_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; SEOdysseyAudit/1.0)"}
ROBOTS_TIMEOUT = 10


def _evaluate_robots(content, http_status, test_path):
    """
    Чистая функция: по содержимому robots.txt и HTTP-статусу решает,
    какие AI-боты допущены к test_path. Тестируется без сети.
    """
    if http_status is None or http_status >= 500:
        return {"status": "unknown", "bots": [], "blocked_critical": []}

    # parse_robots сам применяет RFC 9309: 401/403 — запрет всего,
    # прочие 4xx — robots.txt нет, разрешено всё.
    robots = parse_robots(content, http_status)

    bots = []
    blocked_critical = []
    for b in AI_BOTS:
        allowed = robots.check(b["token"], test_path)[0]
        bots.append({
            "token": b["token"],
            "label": b["label"],
            "vendor": b["vendor"],
            "critical": b["critical"],
            "allowed": allowed,
        })
        if b["critical"] and not allowed:
            blocked_critical.append(b["label"])

    if http_status in (401, 403):
        status = "blocked_site"
    elif http_status >= 400:
        status = "no_robots"
    else:
        status = "ok"

    return {"status": status, "bots": bots, "blocked_critical": blocked_critical}


def check_ai_bots(url, timeout=ROBOTS_TIMEOUT):
    """
    Главная функция. Принимает любой URL страницы сайта,
    тянет <scheme>://<host>/robots.txt и проверяет AI-ботов.
    """
    parsed = urlparse(url if "://" in url else "https://" + url)
    if not parsed.netloc:
        return {"robots_url": None, "status": "error",
                "error": "Не удалось разобрать URL", "bots": [],
                "blocked_critical": [], "summary": "Некорректный URL"}

    robots_url = urlunparse((parsed.scheme or "https", parsed.netloc, "/robots.txt", "", "", ""))
    test_path = parsed.path or "/"

    try:
        resp = safe_get(robots_url, headers=DEFAULT_HEADERS, timeout=timeout)
        http_status = resp.status_code
        content = resp.text if http_status < 400 else ""
    except httpx.RequestError as e:
        return {"robots_url": robots_url, "status": "error",
                "error": str(e), "http_status": None, "bots": [],
                "blocked_critical": [], "summary": "robots.txt недоступен"}

    result = _evaluate_robots(content, http_status, test_path)
    result["robots_url"] = robots_url
    result["http_status"] = http_status
    result["error"] = None

    if result["status"] == "blocked_site":
        result["summary"] = "robots.txt отдаёт 401/403 — сайт закрыт для обхода целиком"
    elif result["status"] == "unknown":
        result["summary"] = "Не удалось определить (сервер вернул 5xx)"
    elif result["blocked_critical"]:
        result["summary"] = "Заблокированы ключевые AI-боты: " + ", ".join(result["blocked_critical"])
    else:
        result["summary"] = "Все ключевые AI-боты допущены"

    return result