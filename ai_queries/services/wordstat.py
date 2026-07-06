from __future__ import annotations

import os
import re
from datetime import timedelta
from typing import Optional

import httpx
from django.utils import timezone

WORDSTAT_URL = "https://searchapi.api.cloud.yandex.net/v2/wordstat/topRequests"
FOLDER_ID = os.getenv("YANDEX_FOLDER_ID", "b1guvrpunctfrsd8jlit")
TIMEOUT = 10.0
CACHE_TTL_DAYS = 30

# Стоп-слова для нормализации AI-запросов в короткие сиды
_RU_STOP_WORDS = {
    "как", "что", "где", "когда", "почему", "зачем", "чем", "кто",
    "какой", "какая", "какие", "какое", "какого", "какому",
    "который", "которой", "которая", "которые", "которого",
    "это", "эти", "эта", "эту", "этот", "этого", "этой",
    "тот", "та", "те", "того", "той",
    "такой", "такая", "такие", "такое",        # ← добавлено
    "или", "и", "а", "но", "для", "в", "на", "с", "по",
    "из", "к", "от", "до", "за", "при", "без", "под", "над",
    "между", "через", "после", "перед", "если", "то", "ли", "же",
    "об", "о", "со", "ко", "во", "не", "ни", "да", "он", "она",
    "можно", "нужно", "надо", "стоит", "хочу", "хочет",
    "нужен", "нужна", "нужны", "нельзя", "следует",   # ← добавлено
    "делать", "сделать", "получить", "узнать", "найти", "выбрать",
    "использовать", "избежать", "предотвратить", "помочь",
    "помогают", "помогает", "помогать",                # ← добавлено
    "позволяет", "позволить", "является", "являются",  # ← добавлено
    "автоматизировать", "оптимизировать",              # ← добавлено
    "стать", "стало", "стали", "есть", "будет",
    "лучшие", "лучший", "лучшая", "топ", "главные", "основные",
    "самые", "самый", "самая", "новый", "новая", "новые",
    "чтобы", "потому", "поэтому", "также", "тоже", "еще", "уже",
    "процесс", "процессы",                             # ← добавлено (слишком общие)
}


def _is_verb_form(word: str) -> bool:
    """Эвристика: глагольные формы по окончаниям."""
    if len(word) < 7:
        return False
    endings = ("овать", "евать", "ивать", "ировать", "ають", "яют", "ают", "тся", "ться")
    return any(word.endswith(e) for e in endings)


def make_wordstat_seed(query: str, max_words: int = 3) -> str:
    text = (query or "").rstrip("?").strip().lower()
    text = re.sub(r"[^\w\s\-]", " ", text)
    words = text.split()
    keywords = [
        w for w in words
        if w not in _RU_STOP_WORDS
        and len(w) >= 3
        and not _is_verb_form(w)
    ]
    if not keywords:
        keywords = [w for w in words if w not in _RU_STOP_WORDS and len(w) >= 3]
    if not keywords:
        keywords = [w for w in words if len(w) >= 3]
    seed = " ".join(keywords[:max_words])
    return seed if seed else text[:40]


def _get_api_key() -> Optional[str]:
    return os.getenv("YANDEX_WORDSTAT_API_KEY")


def _fetch_from_api(seed: str) -> Optional[int]:
    """Запрашивает totalCount для уже нормализованного seed."""
    api_key = _get_api_key()
    if not api_key or not seed:
        return None
    try:
        response = httpx.post(
            WORDSTAT_URL,
            headers={
                "Authorization": f"Api-Key {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "phrase": seed,
                "folderId": FOLDER_ID,
                "numPhrases": 1,
            },
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
        total = int(data.get("totalCount", 0) or 0)
        return total if total > 0 else None
    except Exception:
        return None


def get_total_count(phrase: str) -> Optional[int]:
    from ai_queries.models import WordstatCache

    seed = make_wordstat_seed(phrase)
    cutoff = timezone.now() - timedelta(days=CACHE_TTL_DAYS)
    cached = WordstatCache.objects.filter(phrase=seed, updated_at__gte=cutoff).first()
    if cached is not None:
        return cached.count

    count = _fetch_from_api(seed)
    WordstatCache.objects.update_or_create(
        phrase=seed,
        defaults={"count": count},
    )
    return count


def get_queries_counts(queries: list[str]) -> dict[str, Optional[int]]:
    from ai_queries.models import WordstatCache

    if not queries:
        return {}

    # Маппинг: оригинальный запрос → seed
    seed_for_query = {q: make_wordstat_seed(q) for q in queries}
    unique_seeds = list(set(seed_for_query.values()))

    cutoff = timezone.now() - timedelta(days=CACHE_TTL_DAYS)

    # Берём из кэша по seeds
    cached_qs = WordstatCache.objects.filter(
        phrase__in=unique_seeds,
        updated_at__gte=cutoff,
    )
    cached_map = {c.phrase: c.count for c in cached_qs}

    # Запрашиваем незакэшированные seeds
    to_fetch = [s for s in unique_seeds if s not in cached_map]
    for seed in to_fetch:
        count = _fetch_from_api(seed)
        WordstatCache.objects.update_or_create(
            phrase=seed,
            defaults={"count": count},
        )
        cached_map[seed] = count

    # Ключи в результате — оригинальные запросы
    return {q: cached_map.get(seed_for_query[q]) for q in queries}
