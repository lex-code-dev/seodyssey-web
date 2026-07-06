from __future__ import annotations

import re
from collections import Counter

NOISE_TERMS = {
    "cookie", "cookies", "политика конфиденциальности",
    "персональные данные", "пользовательское соглашение", "карта сайта",
}

BRAND_STOP_WORDS = {
    "ооо", "ао", "зао", "ип", "компания", "group", "company", "официальный", "сайт",
}

INTENT_PATTERNS = {
    "информационный": re.compile(
        r"(что так|что это|как работ|как устро|как использ|как выбр|как настро|как провер|"
        r"как узна|как получ|как избеж|как исправ|как часто|для чего|кому подход|"
        r"почему|зачем|что делать|какие|какой|чем отлич|что такое|что значит|"
        r"ошибк|принцип|способ|метод|руководств|инструкци)",
        re.IGNORECASE
    ),
    "коммерческий": re.compile(
        r"(цена|сколько стоит|стоимость|тариф|купить|заказать|внедр|подключ|оформ|приобрест)",
        re.IGNORECASE
    ),
    "сравнение": re.compile(
        r"(сравн|альтернатив|лучше|отличи|какой лучш|какой выбрать|топ|рейтинг сервис)",
        re.IGNORECASE
    ),
    "локальный": re.compile(
        r"(в .*город|в .*район|где .*в|рядом|екатеринбург|москва|санкт)",
        re.IGNORECASE
    ),
    "репутация": re.compile(
        r"(отзыв|плюсы|минусы|риск|надежн|мнени|проблем)",
        re.IGNORECASE
    ),
}


def _normalize(text: str) -> str:
    value = (text or "").lower()
    value = re.sub(r"[«»\"'`]+", " ", value)
    value = re.sub(r"[^a-zа-я0-9\s\-]", " ", value, flags=re.IGNORECASE)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def _brand_forms(brand: str) -> list[str]:
    norm = _normalize(brand)
    if not norm:
        return []
    forms = {norm}
    for token in norm.split():
        if len(token) < 4 or token in BRAND_STOP_WORDS:
            continue
        forms.add(token)
    return sorted(forms, key=len, reverse=True)


def query_mentions_brand(query: str, brand: str) -> bool:
    qn = _normalize(query)
    if not qn:
        return False
    return any(form in qn for form in _brand_forms(brand))


def detect_intent(query: str) -> str:
    for name, pattern in INTENT_PATTERNS.items():
        if pattern.search(query or ""):
            return name
    return "прочее"


def score_query(query: str, brand: str, theme: list[str]) -> dict:
    q = (query or "").strip()
    issues: list[str] = []
    score = 100
    intent = detect_intent(q)
    has_brand = query_mentions_brand(q, brand)

    if len(q) < 18:
        score -= 20
        issues.append("слишком короткий")
    if not q.endswith("?"):
        score -= 8
        issues.append("нет вопросительного знака")
    if any(term in q.lower() for term in NOISE_TERMS):
        score -= 45
        issues.append("служебная тематика")

    letters = re.findall(r"[A-Za-zА-Яа-яЁё]", q)
    cyrillic = re.findall(r"[А-Яа-яЁё]", q)
    if letters and not cyrillic:
        score -= 40
        issues.append("похоже на транслит")

    theme_tokens: set[str] = set()
    for t in theme or []:
        t_norm = _normalize(t)
        for token in t_norm.split():
            if len(token) >= 4:
                theme_tokens.add(token)

    if theme_tokens:
        q_norm = _normalize(q)
        if not any(tok in q_norm for tok in theme_tokens):
            score -= 10
            issues.append("слабая связь с тематикой")

    if intent == "прочее":
        score -= 12
        issues.append("неочевидный интент")

    return {
        "query": q,
        "score": max(0, min(100, score)),
        "intent": intent,
        "brand_mentioned": has_brand,
        "issues": issues,
    }


def evaluate_query_set(queries: list[str], brand: str, theme: list[str]) -> dict:
    rows = [score_query(q, brand=brand, theme=theme) for q in queries]
    total = len(rows)
    if total == 0:
        return {
            "count": 0,
            "average_score": 0.0,
            "brand_ratio": 0.0,
            "intent_distribution": {},
            "weak_queries": [],
            "query_scores": [],
        }

    avg = sum(r["score"] for r in rows) / total
    brand_count = sum(1 for r in rows if r["brand_mentioned"])
    intents = Counter(r["intent"] for r in rows)
    weak = [r for r in rows if r["score"] < 65]

    return {
        "count": total,
        "average_score": round(avg, 2),
        "brand_ratio": round(brand_count / total, 3),
        "intent_distribution": dict(intents),
        "weak_queries": weak[:10],
        "query_scores": rows,
    }