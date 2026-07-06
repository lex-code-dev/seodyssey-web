from __future__ import annotations
import os
import re
import time
import hashlib
from urllib.parse import urlparse
from typing import Optional
import httpx
from django.core.cache import cache

GENSEARCH_CACHE_TTL = 7 * 24 * 60 * 60  # 7 дней

GENSEARCH_URL = "https://searchapi.api.cloud.yandex.net/v2/gen/search"
FOLDER_ID = os.getenv("YANDEX_FOLDER_ID", "b1guvrpunctfrsd8jlit")
TIMEOUT = 20.0
REQUEST_DELAY = 3.0  # обязательная пауза между запросами


def _get_api_key() -> Optional[str]:
    return os.getenv("YANDEX_WORDSTAT_API_KEY")


def _extract_domain(url: str) -> str:
    """Извлекает hostname без www."""
    try:
        host = urlparse(url).hostname or ""
        return host.removeprefix("www.")
    except Exception:
        return ""


# Rule-based классификация источников цитирования.
# Ключ — подстрока домена, значение — тип. Первое совпадение выигрывает.
# Это стартовый справочник, расширяется по мере реальных данных.
_SOURCE_RULES = [
    ("marketplace", ("ozon.", "wildberries.", "market.yandex", "avito.", "aliexpress.", "dns-shop.", "citilink.")),
    ("media", ("rbc.ru", "kommersant.ru", "vc.ru", "habr.com", "rg.ru", "lenta.ru", "forbes.ru", "tass.ru", "vedomosti.ru")),
    ("social", ("vk.com", "t.me", "telegram.", "youtube.", "youtu.be", "dzen.ru", "zen.yandex", "ok.ru", "rutube.")),
    ("reference", ("wikipedia.org", "wikidata.org", "bigenc.ru")),
]


def _classify_source(domain: str, brand_domain: str) -> str:
    """
    Тип источника цитирования: own / marketplace / media / social / reference / other.
    Rule-based, без внешних вызовов. brand_domain → own.
    """
    if not domain:
        return "other"
    d = domain.lower()
    if brand_domain and (d == brand_domain.lower() or d.endswith("." + brand_domain.lower())):
        return "own"
    for source_type, needles in _SOURCE_RULES:
        if any(n in d for n in needles):
            return source_type
    return "other"

def _mentions_brand(text: str, brand: str) -> bool:
    """Проверяет упоминание бренда в тексте (регистронезависимо)."""
    if not brand or not text:
        return False
    pattern = re.compile(re.escape(brand.strip()), re.IGNORECASE)
    return bool(pattern.search(text))


def check_single_query(query: str, brand: str, brand_domain: str) -> dict:
    """
    Прогоняет один запрос через Yandex GenSearch.
    Возвращает метрики для этого запроса.
    """
    # --- кэш: повторный аудит того же запроса не бьёт по платному API ---
    raw_key = f"{query.strip().lower()}|{brand.strip().lower()}|{brand_domain.strip().lower()}"
    cache_key = "gensearch:" + hashlib.md5(raw_key.encode("utf-8")).hexdigest()
    cached = cache.get(cache_key)
    if cached is not None:
        cached["from_cache"] = True
        return cached
    # --- конец проверки кэша ---

    api_key = _get_api_key()
    result = {
        "query": query,
        "triggered": False,       # AI Trigger Rate
        "brand_mentioned": False,  # Brand Mention Rate
        "brand_cited": False,      # Citation Rate
        "competitors": [],         # для Share of Voice
        "cited_sources": [],       # [{domain, type}] — классификация источников
        "error": None,
        "from_cache": False,       # был ли результат взят из кэша (не из API)
    }

    if not api_key:
        result["error"] = "no_api_key"
        return result

    try:
        response = httpx.post(
            GENSEARCH_URL,
            headers={
                "Authorization": f"Api-Key {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "messages": [{"role": "ROLE_USER", "content": query}],
                "folderId": FOLDER_ID,
            },
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()

        # API возвращает список, берём первый элемент
        item = data[0] if isinstance(data, list) else data

        if item.get("isAnswerRejected", True):
            _cache_set(cache_key, result)
            return result  # triggered=False (валидный платный ответ — кэшируем)

        result["triggered"] = True

        # Brand Mention Rate
        content = item.get("message", {}).get("content", "")
        result["brand_mentioned"] = _mentions_brand(content, brand)

        # Citation Rate + конкуренты
        sources = [s for s in item.get("sources", []) if s.get("used")]
        cited_domains = []
        for s in sources:
            domain = _extract_domain(s.get("url", ""))
            if domain:
                cited_domains.append(domain)
        result["brand_cited"] = brand_domain in cited_domains
        result["competitors"] = [d for d in cited_domains if d != brand_domain]
        # Классификация источников (own/marketplace/media/social/reference/other).
        # Сохраняем порядок и дубли как есть — агрегацию делаем выше по стеку.
        result["cited_sources"] = [
            {"domain": d, "type": _classify_source(d, brand_domain)}
            for d in cited_domains
        ]


    except Exception as e:

        result["error"] = str(e)

    if result["error"] is None:
        _cache_set(cache_key, result)

    return result


def _cache_set(cache_key: str, result: dict) -> None:
    """Кладёт успешный результат в кэш. Ошибки не кэшируются."""
    try:
        cache.set(cache_key, result, GENSEARCH_CACHE_TTL)
    except Exception:
        pass  # сбой кэша не должен ронять аудит

def run_geo_check(queries: list[str], brand: str, brand_url: str) -> dict:
    """
    Прогоняет список запросов и считает итоговые GEO-метрики.
    Возвращает словарь для сохранения в AIQueryResult.visibility.
    """
    brand_domain = _extract_domain(brand_url)
    results = []

    for i, query in enumerate(queries):
        if i > 0:
            time.sleep(REQUEST_DELAY)
        r = check_single_query(query, brand, brand_domain)
        results.append(r)

    # Считаем метрики
    triggered = [r for r in results if r["triggered"]]
    trigger_count = len(triggered)
    total = len(results)

    mention_count = sum(1 for r in triggered if r["brand_mentioned"])
    citation_count = sum(1 for r in triggered if r["brand_cited"])

    # Share of Voice: частота доменов конкурентов
    from collections import Counter
    all_competitors = []
    for r in triggered:
        all_competitors.extend(r["competitors"])
    competitor_counts = dict(Counter(all_competitors).most_common(10))
    # Earned media gap: разбивка всех цитирований по типу источника
    source_types = Counter()
    for r in triggered:
        for src in r.get("cited_sources", []):
            source_types[src["type"]] += 1
    source_breakdown = {
        t: source_types.get(t, 0)
        for t in ("own", "marketplace", "media", "social", "reference", "other")
    }

    # Visibility Score = (mention_rate + citation_rate) / 2 * 100
    mention_rate = mention_count / trigger_count if trigger_count else 0
    citation_rate = citation_count / trigger_count if trigger_count else 0
    trigger_rate = trigger_count / total if total else 0

    # Бонус за "горячесть" ниши (0-15)
    niche_bonus = round(trigger_rate * 15, 1)
    visibility_score = round((mention_rate + citation_rate) / 2 * 100 + niche_bonus, 1)

    return {
        "geo": {
            "trigger_rate": round(trigger_rate * 100, 1),
            "mention_rate": round(mention_rate * 100, 1),
            "citation_rate": round(citation_rate * 100, 1),
            "visibility_score": visibility_score,
            "competitors": competitor_counts,
            "source_breakdown": source_breakdown,
            "queries_detail": results,
            "brand_domain": brand_domain,
        }
    }