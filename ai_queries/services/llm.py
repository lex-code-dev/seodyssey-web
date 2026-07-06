from __future__ import annotations

import json
import re
from typing import Optional

from openai import OpenAI

from ai_queries.services.types import PageData

_client = OpenAI()  # берёт OPENAI_API_KEY из окружения автоматически

INTENT_HINTS = {
    "informational": ("что такое", "как работает", "как выбрать", "как использовать", "ошибки", "критерии"),
    "commercial": ("цена", "стоимость", "сколько стоит", "тариф", "заказать", "купить", "внедрение", "интеграция"),
    "comparison": ("сравнение", "чем отличается", "альтернатива", "лучше"),
    "reviews": ("отзывы", "мнение", "рейтинг", "минусы", "плюсы"),
    "local": ("в москве", "в санкт-петербурге", "в екатеринбурге", "рядом"),
}

INJECTION_PATTERNS = [
    r"ignore\s+previous\s+instructions",
    r"system\s+prompt",
    r"you\s+are\s+chatgpt",
    r"следуй\s+этим\s+инструкциям",
    r"игнорируй\s+предыдущие\s+инструкции",
    r"выполни\s+следующее\s+задание",
]


def _sanitize_for_prompt(text: str) -> str:
    cleaned = text or ""
    for pattern in INJECTION_PATTERNS:
        cleaned = re.sub(pattern, " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"[<>{}]{2,}", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _extract_json(content: str) -> Optional[dict]:
    content = (content or "").strip()
    if not content:
        return None
    if content.startswith("```"):
        content = re.sub(r"^```(json)?", "", content).strip()
        content = re.sub(r"```$", "", content).strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", content, flags=re.DOTALL)
        if not match:
            return None
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None


def call_openai(prompt: str) -> Optional[dict]:
    try:
        response = _client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": "Ты аккуратный аналитик видимости бренда в AI. Отвечай строго JSON.",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content
        return _extract_json(content)
    except Exception:
        return None


def call_openai_text(prompt: str, system: str = "") -> Optional[str]:
    """Текстовый ответ (без forced-JSON). Для AI-резюме GEO-отчёта."""
    try:
        response = _client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system or "Ты SEO-аналитик. Пиши кратко и по делу, без воды."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
        )
        return (response.choices[0].message.content or "").strip()
    except Exception:
        return None


def _site_excerpt(data: PageData, max_len: int) -> str:
    raw = " ".join([
        data.title or "",
        data.description or "",
        data.h1 or "",
        " ".join(data.h2 or []),
        data.text or "",
    ])
    cleaned = _sanitize_for_prompt(raw)
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[: max_len - 1] + "…"


def _context_block(homepage: PageData, context_pages: list[PageData] | None = None) -> str:
    parts = [
        f"[HOME]\nURL: {homepage.url}\nTITLE: {homepage.title}\n"
        f"DESCRIPTION: {homepage.description}\nH1: {homepage.h1}\n"
        f"H2: {', '.join(homepage.h2)}\nTEXT: {_site_excerpt(homepage, max_len=1200)}"
    ]
    for idx, page in enumerate(context_pages or [], start=1):
        parts.append(
            f"[PAGE {idx}]\nURL: {page.url}\nTITLE: {page.title}\n"
            f"DESCRIPTION: {page.description}\nH1: {page.h1}\n"
            f"H2: {', '.join(page.h2)}\nTEXT: {_site_excerpt(page, max_len=650)}"
        )
    return "\n\n".join(parts)


def _normalize_query(query: str) -> str:
    text = (query or "").strip()
    text = re.sub(r"\s+", " ", text)
    if text and not text.endswith("?"):
        text = text.rstrip(".!") + "?"
    return text


def _brand_forms(brand: str) -> list[str]:
    norm = re.sub(r"[^a-zа-я0-9\s-]", " ", (brand or "").lower(), flags=re.IGNORECASE)
    norm = re.sub(r"\s+", " ", norm).strip()
    if not norm:
        return []
    forms = {norm}
    for token in norm.split():
        if len(token) >= 4:
            forms.add(token)
    return sorted(forms, key=len, reverse=True)


def _mentions_brand(query: str, brand: str) -> bool:
    qn = re.sub(r"[^a-zа-я0-9\s-]", " ", (query or "").lower(), flags=re.IGNORECASE)
    qn = re.sub(r"\s+", " ", qn).strip()
    if not qn:
        return False
    return any(form and form in qn for form in _brand_forms(brand))


def _profile_prompt(context: str) -> str:
    return f"""
Шаг 1/2. Построй профиль бизнеса по данным сайта.
Верни JSON по схеме:
{{
  "brand": "string",
  "theme": ["string", "string"],
  "geo": {{"country": "string", "city": "string", "confidence": 0.0}}
}}

Правила:
- Язык: русский.
- theme: 1-2 бизнес-категории (конкретно, без воды).
- Если город не определен, верни пустую строку, не выдумывай.
- Не используй служебные/юридические темы.

Данные сайта:
{context}
"""


def _candidate_prompt(profile: dict, context: str, count: int = 40) -> str:
    brand = profile.get("brand", "")
    theme = ", ".join(profile.get("theme") or [])
    geo = profile.get("geo") or {}
    city = geo.get("city", "")
    country = geo.get("country", "")
    geo_str = ", ".join(filter(None, [city, country]))

    return f"""
Шаг 2/2. Сгенерируй запросы для GEO/AEO-аудита AI-видимости бренда.
Верни JSON: {{"ai_queries": [{{"query": "string", "type": "string"}}]}}

Сгенерируй три типа запросов:

1. БРЕНДОВЫЕ (10 штук) — прямо упоминают название бренда.
   Примеры формул:
   - Что такое [бренд]?
   - Чем занимается [бренд]?
   - Какие услуги оказывает [бренд]?
   - Можно ли доверять [бренд]?
   - Отзывы о [бренд]
   - Сколько стоит [бренд]?
   Для type укажи: "брендовый"

2. СЛЕПЫЕ (20 штук) — без упоминания бренда, описывают задачу или проблему пользователя.
   Примеры формул:
   - Как выбрать [товар/услугу]?
   - Какие компании помогают с [задача]?
   - Что делать, если [ситуация]?
   - Лучшие сервисы для [задача]
   - Где заказать [услуга] в [город]?
   Для type укажи: "слепой"

3. СРАВНИТЕЛЬНЫЕ (10 штук) — сравнивают категорию или подход, без прямого упоминания бренда.
   Примеры формул:
   - Чем отличается [подход А] от [подход Б]?
   - Что лучше: [вариант А] или [вариант Б]?
   - Как выбрать между [X] и [Y]?
   - Альтернативы [категория решений]
   Для type укажи: "сравнительный"

Правила:
- Язык: русский, только кириллица.
- Каждый вопрос заканчивается "?" (кроме формул типа "Отзывы о..." или "Топ...").
- Формулировки как у реальных пользователей.
- Без дублей, без служебных/юридических тем.
- Если есть город ({geo_str}), добавь локальные вопросы в слепые и брендовые.

Бренд: {brand}
Тематика: {theme}
Гео: {geo_str}

Данные сайта:
{context}
"""


def _normalize_profile(profile: dict) -> Optional[dict]:
    if not isinstance(profile, dict):
        return None
    raw_theme = profile.get("theme")
    if isinstance(raw_theme, str):
        theme = [v.strip() for v in re.split(r"[,;|]", raw_theme) if v.strip()]
    elif isinstance(raw_theme, list):
        theme = [str(v).strip() for v in raw_theme if str(v).strip()]
    else:
        theme = []
    raw_geo = profile.get("geo")
    if not isinstance(raw_geo, dict):
        raw_geo = {}
    country = str(raw_geo.get("country") or "").strip()
    city = str(raw_geo.get("city") or "").strip()
    try:
        confidence = float(raw_geo.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    if not theme:
        return None
    return {
        "brand": str(profile.get("brand") or "").strip(),
        "theme": theme[:2],
        "geo": {"country": country, "city": city, "confidence": confidence},
    }


def _normalize_queries(payload: dict) -> list[dict]:
    if not isinstance(payload, dict):
        return []
    raw = payload.get("ai_queries")
    if not isinstance(raw, list):
        return []
    result = []
    for item in raw:
        if isinstance(item, str) and len(item.strip()) >= 8:
            result.append({"query": item.strip(), "type": "слепой"})
        elif isinstance(item, dict):
            q = str(item.get("query") or "").strip()
            t = str(item.get("type") or "слепой").strip()
            if len(q) >= 8:
                result.append({"query": q, "type": t})
    return result


def _pick_balanced_queries(candidates: list[dict], brand: str, geo: dict, target: int = 20) -> list[dict]:
    if not candidates:
        return []

    buckets: dict[str, list[dict]] = {
        "брендовый": [],
        "слепой": [],
        "сравнительный": [],
    }
    for item in candidates:
        t = item.get("type", "слепой")
        buckets.setdefault(t, []).append(item)

    selected: list[dict] = []
    seen: set[str] = set()

    def try_take(items: list[dict], limit: int) -> None:
        for item in items:
            if len(selected) >= target or limit <= 0:
                break
            key = item["query"].casefold()
            if key in seen:
                continue
            seen.add(key)
            selected.append(item)
            limit -= 1

    # Квоты: 5 брендовых + 5 сравнительных + 10 слепых
    try_take(buckets["брендовый"], 5)
    try_take(buckets["сравнительный"], 5)
    try_take(buckets["слепой"], 10)

    # Добираем остаток из любых
    all_remaining = [i for i in candidates if i["query"].casefold() not in seen]
    try_take(all_remaining, target - len(selected))

    return selected[:target]


def _run_pipeline(context: str, candidate_count: int) -> Optional[dict]:
    profile_raw = call_openai(_profile_prompt(context))
    profile = _normalize_profile(profile_raw or {})
    if not profile:
        return None

    candidates_payload = call_openai(_candidate_prompt(profile, context, count=candidate_count))
    candidates = _normalize_queries(candidates_payload or {})
    if not candidates:
        return None

    final_items = _pick_balanced_queries(
        candidates,
        brand=str(profile.get("brand") or ""),
        geo=profile.get("geo") or {},
        target=20,
    )
    if not final_items:
        return None

    return {
        "brand": profile.get("brand", ""),
        "theme": profile.get("theme", []),
        "geo": profile.get("geo", {}),
        "ai_queries": [item["query"] for item in final_items],
        "query_types": {item["query"]: item["type"] for item in final_items},
    }


def try_llm_analyze(data: PageData, context_pages: list[PageData] | None = None) -> Optional[dict]:
    full_context = _context_block(data, context_pages=context_pages)
    result = _run_pipeline(full_context, candidate_count=40)
    if result:
        return result
    # Fallback: повторяем с укороченным контекстом
    slim_context = _context_block(data, context_pages=None)
    return _run_pipeline(slim_context, candidate_count=30)


def try_llm_expand_queries(
    data: PageData,
    context_pages: list[PageData] | None,
    brand: str,
    theme: list[str],
    geo: dict,
    existing: list[str],
    need_count: int,
) -> list[str]:
    if need_count <= 0:
        return []
    context = _context_block(data, context_pages=context_pages)
    prompt = f"""
Дополни список вопросов для AI-выдачи.
Верни JSON: {{"ai_queries": ["string"]}}

Правила:
- Верни ровно {need_count} новых вопросов.
- Нельзя повторять существующие.
- Язык: русский, кириллица, каждый вопрос заканчивается "?".
- Большая часть запросов без названия бренда.

Бренд: {brand}
Тематика: {", ".join(theme or [])}
Гео: country={geo.get('country', '')}, city={geo.get('city', '')}
Существующие вопросы:
{json.dumps(existing, ensure_ascii=False)}

Сигналы сайта:
{context}
"""
    result = call_openai(prompt)
    if not result or not isinstance(result.get("ai_queries"), list):
        return []
    return [str(q) for q in result.get("ai_queries", [])]


def try_llm_refine_queries(
    brand: str,
    theme: list[str],
    geo: dict,
    queries: list[str],
    target: int = 20,
) -> list[str]:
    if not queries:
        return []
    prompt = f"""
Отредактируй и нормализуй список вопросов.
Верни JSON: {{"ai_queries": ["string"]}}

Правила:
- Верни ровно {target} вопросов.
- Удали дубли и почти-дубли.
- Язык: русский, кириллица, каждый вопрос заканчивается "?".
- Упоминаний бренда должно быть меньше, чем тематических запросов.

Бренд: {brand}
Тематика: {", ".join(theme or [])}
Гео: country={geo.get('country', '')}, city={geo.get('city', '')}
Исходные вопросы:
{json.dumps(queries, ensure_ascii=False)}
"""
    result = call_openai(prompt)
    if not result or not isinstance(result.get("ai_queries"), list):
        return []
    return [str(q) for q in result.get("ai_queries", [])]

def generate_geo_summary(brand: str, geo: dict, chatgpt: dict) -> str:
    """AI-резюме по результатам GEO-аудита. Возвращает текст или ''."""
    geo = geo or {}
    chatgpt = chatgpt or {}
    competitors = geo.get("competitors") or {}
    top_comp = ", ".join(list(competitors.keys())[:5]) or "—"

    prompt = (
        f"Бренд: {brand}\n"
        f"Видимость в Яндекс (Нейро):\n"
        f"- AI Trigger Rate: {geo.get('trigger_rate', 0)}%\n"
        f"- Brand Mention Rate: {geo.get('mention_rate', 0)}%\n"
        f"- Citation Rate: {geo.get('citation_rate', 0)}%\n"
        f"- Share of Voice: {geo.get('visibility_score', 0)}%\n"
        f"Видимость в ChatGPT:\n"
        f"- Brand Mention Rate: {chatgpt.get('mention_rate', 0)}%\n"
        f"- Citation Rate: {chatgpt.get('citation_rate', 0)}%\n"
        f"- Share of Voice: {chatgpt.get('visibility_score', 0)}%\n"
        f"Чаще всего в AI-ответы попадают конкуренты: {top_comp}\n\n"
        f"Напиши короткое резюме (3-5 предложений) для владельца бизнеса: "
        f"что показывают эти цифры про видимость бренда в AI-поиске и на чём "
        f"сосредоточиться в первую очередь. Без маркдауна, простым текстом."
    )
    return call_openai_text(prompt) or ""