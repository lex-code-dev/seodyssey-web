from celery import shared_task
from django.utils import timezone


@shared_task
def run_geo_audit_task(audit_id: int):
    from ai_queries.models import GeoAuditResult
    from ai_queries.services.gensearch import check_single_query, _extract_domain
    from ai_queries.services.visibility import check_single_query_chatgpt
    import time

    REQUEST_DELAY = 3.0

    try:
        audit = GeoAuditResult.objects.get(id=audit_id)
    except GeoAuditResult.DoesNotExist:
        return {"error": "not found"}

    audit.status = GeoAuditResult.STATUS_RUNNING
    audit.progress = 0
    audit.save(update_fields=["status", "progress"])

    brand_domain = _extract_domain(audit.brand_url)
    yandex_results = []
    chatgpt_results = []

    try:
        made_api_call = False
        for i, query in enumerate(audit.queries):
            if made_api_call:
                time.sleep(REQUEST_DELAY)

            # Яндекс
            r = check_single_query(query, audit.brand, brand_domain)
            yandex_results.append(r)
            made_api_call = not r.get("from_cache", False)

            # ChatGPT
            cg = check_single_query_chatgpt(query, audit.brand, audit.brand_url)
            chatgpt_results.append(cg)

            audit.progress = i + 1
            audit.save(update_fields=["progress"])

        # Метрики Яндекс
        from collections import Counter
        triggered = [r for r in yandex_results if r["triggered"]]
        trigger_count = len(triggered)
        total = len(yandex_results)
        mention_count = sum(1 for r in triggered if r["brand_mentioned"])
        citation_count = sum(1 for r in triggered if r["brand_cited"])
        all_competitors = []
        for r in triggered:
            all_competitors.extend(r["competitors"])
        competitor_counts = dict(Counter(all_competitors).most_common(10))
        # Earned media gap: разбивка цитирований по типу источника
        # Собираем все цитируемые домены со всех запросов
        all_cited_domains = []
        for r in triggered:
            for src in r.get("cited_sources", []):
                all_cited_domains.append(src["domain"])
        # own определяем сами; остальное классифицирует AI (один batch на аудит, с кешем)
        from ai_queries.services.source_classifier import classify_domains
        foreign = [d for d in all_cited_domains if d != brand_domain]
        domain_types = classify_domains(foreign)
        # own → всегда own; остальные → тип из AI (по умолчанию other)
        source_types = Counter()
        for d in all_cited_domains:
            if d == brand_domain:
                source_types["own"] += 1
            else:
                source_types[domain_types.get(d, "other")] += 1
        source_breakdown = {
            t: source_types.get(t, 0)
            for t in ("own", "marketplace", "media", "social", "reference", "other")
        }
        mention_rate = mention_count / trigger_count if trigger_count else 0
        citation_rate = citation_count / trigger_count if trigger_count else 0
        trigger_rate = trigger_count / total if total else 0
        niche_bonus = round(trigger_rate * 15, 1)
        yandex_score = round((mention_rate + citation_rate) / 2 * 100 + niche_bonus, 1)

        # Метрики ChatGPT
        cg_total = len(chatgpt_results)
        cg_mention_count = sum(1 for r in chatgpt_results if r["brand_mentioned"])
        cg_citation_count = sum(1 for r in chatgpt_results if r["brand_cited"])
        cg_mention_rate = cg_mention_count / cg_total if cg_total else 0
        cg_citation_rate = cg_citation_count / cg_total if cg_total else 0
        cg_score = round((cg_mention_rate + cg_citation_rate) / 2 * 100, 1)
        # Агрегат тональности по ChatGPT-упоминаниям (для сводной плашки в отчёте)
        cg_tones = [r.get("brand_tone") for r in chatgpt_results if r["brand_mentioned"] and r.get("brand_tone")]
        if not cg_tones:
            cg_tone_summary = "unknown"  # ИИ почти не знает бренд
        elif "negative" in cg_tones:
            cg_tone_summary = "negative"  # есть хотя бы один негатив — поднимаем флаг
        elif "positive" in cg_tones:
            cg_tone_summary = "positive"
        else:
            cg_tone_summary = "neutral"

        result = {
            "geo": {
                "trigger_rate": round(trigger_rate * 100, 1),
                "mention_rate": round(mention_rate * 100, 1),
                "citation_rate": round(citation_rate * 100, 1),
                "visibility_score": yandex_score,
                "competitors": competitor_counts,
                "source_breakdown": source_breakdown,
                "queries_detail": yandex_results,
                "brand_domain": brand_domain,
            },
            "chatgpt": {
                "mention_rate": round(cg_mention_rate * 100, 1),
                "citation_rate": round(cg_citation_rate * 100, 1),
                "visibility_score": cg_score,
                "tone_summary": cg_tone_summary,
                "queries_detail": chatgpt_results,
            },
        }

        audit.result = result
        audit.status = GeoAuditResult.STATUS_DONE
        audit.finished_at = timezone.now()
        audit.save()
        return {"audit_id": audit.id, "status": "done"}

    except Exception as e:
        audit.status = GeoAuditResult.STATUS_ERROR
        audit.error = str(e)
        audit.finished_at = timezone.now()
        audit.save()
        return {"audit_id": audit.id, "status": "error", "error": str(e)}