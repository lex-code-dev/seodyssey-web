"""AI-классификация доменов цитирования по типу источника.

own определяется снаружи (== домен бренда) и сюда не передаётся.
Категории, которые отдаёт AI: marketplace / media / social / reference / other.
Результат кешируется по домену (Redis, 30 дней) — тип домена стабилен.
Сбой AI не критичен: при ошибке домен получает тип 'other'.
"""
from __future__ import annotations

import json
from django.core.cache import cache

_CACHE_PREFIX = "srcclass:"
_CACHE_TTL = 60 * 60 * 24 * 30  # 30 дней
_VALID = {"marketplace", "media", "social", "reference", "other"}


def classify_domains(domains: list[str]) -> dict[str, str]:
    """
    Принимает список доменов (без домена бренда), возвращает {domain: type}.
    Кеширует по каждому домену. Некешированные классифицирует одним AI-запросом.
    При любой ошибке недостающие домены получают 'other'.
    """
    domains = [d for d in dict.fromkeys(domains) if d]  # уникальные, сохраняя порядок
    if not domains:
        return {}

    result: dict[str, str] = {}
    missing: list[str] = []

    # 1. сначала кеш
    for d in domains:
        cached = cache.get(_CACHE_PREFIX + d)
        if cached in _VALID:
            result[d] = cached
        else:
            missing.append(d)

    # 2. некешированные → один batch-вызов AI
    if missing:
        ai_types = _classify_via_ai(missing)
        for d in missing:
            t = ai_types.get(d, "other")
            if t not in _VALID:
                t = "other"
            result[d] = t
            cache.set(_CACHE_PREFIX + d, t, _CACHE_TTL)

    return result


def _classify_via_ai(domains: list[str]) -> dict[str, str]:
    """Один запрос к модели на весь список. При сбое — пустой dict (→ вызовут 'other')."""
    from ai_queries.services.llm import call_openai

    domains_list = "\n".join(f"- {d}" for d in domains)
    prompt = (
        "Классифицируй каждый домен по типу источника для русскоязычного интернета.\n"
        "Допустимые типы (выбирай строго один из них):\n"
        "- marketplace — маркетплейсы и агрегаторы-каталоги (ozon, wildberries, 2gis, zoon, avito, hh)\n"
        "- media — СМИ, новостные и отраслевые издания (rbc, vc.ru, sostav, vedomosti)\n"
        "- social — соцсети, мессенджеры, видеоплатформы (vk, telegram, youtube, dzen)\n"
        "- reference — справочники и энциклопедии (wikipedia, словари, гос-справочники)\n"
        "- other — всё остальное, включая блоги компаний и непонятные домены\n\n"
        f"Домены:\n{domains_list}\n\n"
        'Верни строго JSON-объект вида {"domain.ru": "media", ...} без пояснений и markdown. '
        "Ключи — домены ровно как в списке, значения — один из 5 типов."
    )

    data = call_openai(prompt)
    if not isinstance(data, dict):
        return {}
    # значения нормализуем к нижнему регистру, мусор отсеется в classify_domains
    return {str(k).strip(): str(v).strip().lower() for k, v in data.items()}