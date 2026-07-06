from __future__ import annotations
import re
import json
import hashlib
from typing import Optional
from django.core.cache import cache
from ai_queries.services.llm import _client

CHATGPT_CACHE_TTL = 7 * 24 * 60 * 60  # 7 дней


def _brand_in_text(text: str, brand: str) -> bool:
    """Проверяет, упоминается ли бренд в тексте."""
    if not text or not brand:
        return False
    pattern = re.escape(brand.strip().lower())
    return bool(re.search(pattern, text.lower()))


def check_query_visibility_chatgpt(query: str, brand: str) -> Optional[bool]:
    """
    Отправляет запрос в ChatGPT и проверяет, упомянут ли бренд в ответе.
    Возвращает True/False или None при ошибке.
    """
    if not query or not brand:
        return None
    try:
        response = _client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": query}],
            temperature=0.0,
            max_tokens=512,
        )
        answer = response.choices[0].message.content or ""
        return _brand_in_text(answer, brand)
    except Exception:
        return None


def check_visibility_for_queries(
    queries: list[str],
    brand: str,
) -> dict[str, dict]:
    """
    Проверяет список запросов. Возвращает:
    {
        "Запрос 1": {"chatgpt": True, "yandex": None, "google": None},
        ...
    }
    """
    result = {}
    for query in queries:
        chatgpt = check_query_visibility_chatgpt(query, brand)
        result[query] = {
            "chatgpt": chatgpt,
            "yandex": None,
            "google": None,
        }
    return result


def _chatgpt_cache_set(cache_key: str, result: dict) -> None:
    """Кладёт успешный результат в кэш. Ошибки не кэшируются."""
    try:
        cache.set(cache_key, result, CHATGPT_CACHE_TTL)
    except Exception:
        pass  # сбой кэша не должен ронять аудит


def _classify_brand_in_answer(answer: str, brand: str) -> dict:
    """
    Спрашивает у gpt-4o-mini, ЗНАЕТ ли нейросеть бренд содержательно,
    и если да — какой тон. Возвращает {"knows": bool, "tone": str|None}.
    При любом сбое (сеть/парсинг) -> knows=False, tone=None (как старое поведение).
    """
    if not answer or not brand:
        return {"knows": False, "tone": None}

    system = (
        "Ты анализируешь, как нейросеть упоминает бренд в своём ответе. "
        "Верни СТРОГО JSON и ничего больше: "
        '{"knows": true/false, "tone": "..."}. '
        "knows = false, если в тексте нет содержательной информации о бренде "
        "ИЛИ модель прямо признаёт, что не знает бренд (даже если название встречается). "
        "knows = true только если есть реальное описание/оценка бренда. "
        "tone (только при knows=true) — одно из: positive, neutral, negative; "
        "иначе tone = null."
    )
    user = (
        f"Бренд: {brand}\n\n"
        f"Текст ответа нейросети:\n{answer}\n\n"
        f"Знает ли нейросеть бренд {brand} содержательно, и если да — какой тон?"
    )
    try:
        resp = _client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.0,
        )
        raw = (resp.choices[0].message.content or "").strip()
        raw = raw.replace("```json", "").replace("```", "")
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            return {"knows": False, "tone": None}
        data = json.loads(m.group(0))
        knows = bool(data.get("knows"))
        tone = data.get("tone") if knows else None
        if tone not in ("positive", "neutral", "negative"):
            tone = None
        return {"knows": knows, "tone": tone}
    except Exception:
        return {"knows": False, "tone": None}


def check_single_query_chatgpt(query: str, brand: str, brand_url: str) -> dict:
    """
    Проверяет запрос через ChatGPT: упомянут ли бренд и brand_url в ответе.
    brand_mentioned теперь = содержательное знание бренда (через классификатор),
    а не простое совпадение подстроки. Дополнительно отдаёт brand_tone.
    """
    # --- кэш: повторный аудит того же запроса не бьёт по платному API ---
    raw_key = f"{query.strip().lower()}|{brand.strip().lower()}|{brand_url.strip().lower()}"
    cache_key = "chatgpt:" + hashlib.md5(raw_key.encode("utf-8")).hexdigest()
    cached = cache.get(cache_key)
    if cached is not None:
        cached["from_cache"] = True
        return cached
    # --- конец проверки кэша ---

    try:
        from ai_queries.services.gensearch import _extract_domain
        response = _client.chat.completions.create(
            model="gpt-5-search-api",
            messages=[{"role": "user", "content": query}],
            max_completion_tokens=2000,
        )
        answer = response.choices[0].message.content or ""
        domain = _extract_domain(brand_url)
        brand_cited = bool(domain and domain.lower() in answer.lower())

        verdict = _classify_brand_in_answer(answer, brand)

        result = {
            "query": query,
            "brand_mentioned": verdict["knows"],   # ← честный сигнал вместо подстроки
            "brand_tone": verdict["tone"],         # ← новое: positive/neutral/negative/None
            "brand_cited": brand_cited,
            "from_cache": False,
        }
        _chatgpt_cache_set(cache_key, result)  # кэшируем только успех
        return result
    except Exception:
        return {
            "query": query,
            "brand_mentioned": False,
            "brand_tone": None,
            "brand_cited": False,
            "from_cache": False,
        }