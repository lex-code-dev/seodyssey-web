from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import render, get_object_or_404, redirect
from django.views.decorators.http import require_http_methods, require_POST

from ai_queries.models import AIQueryResult
from ai_queries.services.analyze import analyze


@login_required
@require_http_methods(["GET", "POST"])
def analyze_view(request):
    if request.method == "GET":
        prefill_url = request.GET.get('url', '')
        return render(request, 'ai_queries/analyze.html', {'prefill_url': prefill_url})

    url = request.POST.get('url', '').strip()
    if not url:
        return JsonResponse({'error': 'URL не указан'}, status=400)
    # GEO-аудит недоступен на тарифе Free (дорогой вызов Яндекса).
    from billing.services import get_active_plan
    plan = get_active_plan(request.user)
    if plan is None or plan.slug == "free":
        return JsonResponse({
            "status": "plan_required",
            "error": "GEO-аудит доступен на платных тарифах. Перейдите на «Старт» или выше, чтобы анализировать видимость в ИИ-поиске.",
        }, status=403)
    # Проверяем лимит ДО вызова Яндекса (без списания), чтобы не жечь 108₽ впустую.
    from core.ai_limits import check_ai_limit
    ok, used, limit = check_ai_limit(request.user)
    if not ok:
        return JsonResponse({
            "status": "limit",
            "error": "Лимит AI-проверок исчерпан. Обновится 1-го числа следующего месяца.",
            "ai_used": used,
            "ai_limit": limit,
        }, status=403)
    try:
        result = analyze(url)

        from ai_queries.services.wordstat import get_queries_counts
        queries = result.get('ai_queries', [])
        wordstat_counts = get_queries_counts(queries)
        result['wordstat_counts'] = wordstat_counts

        AIQueryResult.objects.create(
            user=request.user,
            input_url=result.get('input_url', url),
            brand=result.get('brand', ''),
            theme=result.get('theme', []),
            geo=result.get('geo', {}),
            ai_queries=result.get('ai_queries', []),
            quality=result.get('quality', {}),
            elapsed_ms=result.get('elapsed_ms'),
            query_types=result.get('query_types', {}),
        )

        # Списываем лимит только после успешного анализа
        from core.ai_limits import check_and_spend_ai_limit
        ok, used, limit = check_and_spend_ai_limit(request.user)
        if not ok:
            return JsonResponse({
                "status": "limit",
                "error": "Лимит AI-проверок исчерпан. Обновится 1-го числа следующего месяца.",
                "ai_used": used,
                "ai_limit": limit,
            }, status=403)

        result['ai_used'] = used
        result['ai_limit'] = limit
        return JsonResponse(result)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@login_required
def history_view(request):
    items = AIQueryResult.objects.filter(user=request.user).order_by('-created_at')[:50]
    return render(request, 'ai_queries/history.html', {'items': items})


@login_required
def history_detail_view(request, pk):
    from ai_queries.models import WordstatCache
    from ai_queries.services.wordstat import make_wordstat_seed

    item = get_object_or_404(AIQueryResult, pk=pk, user=request.user)

    scores = item.quality.get('query_scores', []) if item.quality else []
    scores_map = {s['query']: s for s in scores}

    queries = item.ai_queries or []

    # Ищем в кэше по seeds, а не по полным запросам
    seeds = [make_wordstat_seed(q) for q in queries]
    cached = WordstatCache.objects.filter(phrase__in=seeds)
    wordstat_map = {c.phrase: c.count for c in cached}

    rows = []
    for q in queries:
        score = scores_map.get(q, {})
        seed = make_wordstat_seed(q)
        rows.append({
            'query': q,
            'intent': score.get('intent', ''),
            'count': wordstat_map.get(seed),
        })

    return render(request, 'ai_queries/history_detail.html', {
        'item': item,
        'rows': rows,
    })


@login_required
@require_POST
def history_clear_view(request):
    AIQueryResult.objects.filter(user=request.user).delete()
    return redirect('ai_queries:history')

@login_required
@require_POST
def check_visibility_view(request, pk):
    import json
    from ai_queries.services.visibility import check_visibility_for_queries

    item = get_object_or_404(AIQueryResult, pk=pk, user=request.user)

    queries = item.ai_queries or []
    brand = item.brand or ""

    if not queries or not brand:
        return JsonResponse({"error": "Нет данных для проверки"}, status=400)

    visibility = check_visibility_for_queries(queries, brand)

    item.visibility = visibility
    item.save(update_fields=["visibility"])

    return JsonResponse({"visibility": visibility})

@login_required
@require_POST
def check_single_visibility_view(request, pk):
    import json
    from ai_queries.services.visibility import check_query_visibility_chatgpt

    item = get_object_or_404(AIQueryResult, pk=pk, user=request.user)

    try:
        body = json.loads(request.body)
        query = body.get('query', '')
    except Exception:
        return JsonResponse({'error': 'bad request'}, status=400)

    if not query or not item.brand:
        return JsonResponse({'error': 'no data'}, status=400)

    result = check_query_visibility_chatgpt(query, item.brand)

    # Сохраняем результат в visibility
    visibility = item.visibility or {}
    visibility[query] = {'chatgpt': result, 'yandex': None, 'google': None}
    item.visibility = visibility
    item.save(update_fields=['visibility'])

    return JsonResponse({'query': query, 'chatgpt': result})

@login_required
@require_http_methods(["GET", "POST"])
def geo_audit_view(request):

    if request.method == "POST":
        brand = request.POST.get("brand", "").strip()
        brand_url = request.POST.get("brand_url", "").strip()
        queries_raw = request.POST.get("queries", "").strip()
        queries = [q.strip() for q in queries_raw.splitlines() if q.strip()]

        if not brand or not brand_url or not queries:
            return render(request, "ai_queries/geo_audit.html", {
                "error": "Заполните все поля.",
                "post": request.POST,
            })

        from ai_queries.models import GeoAuditResult
        from ai_queries.tasks import run_geo_audit_task

        audit = GeoAuditResult.objects.create(
            user=request.user,
            brand=brand,
            brand_url=brand_url,
            queries=queries,
        )
        run_geo_audit_task.delay(audit.id)

        from django.urls import reverse
        return redirect(reverse('ai_queries:geo_audit_status', args=[audit.id]))

    from ai_queries.models import GeoAuditResult
    history = GeoAuditResult.objects.filter(user=request.user).order_by('-created_at')[:20]
    return render(request, "ai_queries/geo_audit.html", {"post": {}, "history": history})


@login_required
def geo_audit_status_view(request, pk):

    from ai_queries.models import GeoAuditResult
    audit = get_object_or_404(GeoAuditResult, id=pk, user=request.user)

    result = audit.result if audit.status == GeoAuditResult.STATUS_DONE else None

    combined_queries = []
    if result:
        yandex_detail = result.get('geo', {}).get('queries_detail', [])
        chatgpt_detail = result.get('chatgpt', {}).get('queries_detail', [])
        chatgpt_map = {r['query']: r for r in chatgpt_detail}
        for r in yandex_detail:
            combined_queries.append({
                'query': r['query'],
                'yandex': r,
                'chatgpt': chatgpt_map.get(r['query'], {}),
            })

    # Источники цитирования → бары + earned media gap (та же логика, что в PDF-билдере)
    _SOURCE_LABELS = {
        "own": "Свой сайт", "marketplace": "Маркетплейсы", "media": "СМИ",
        "social": "Соцсети", "reference": "Справочники", "other": "Прочее",
    }
    breakdown_raw = (result or {}).get("geo", {}).get("source_breakdown") or {}
    source_total = sum(breakdown_raw.values())
    source_breakdown = []
    if source_total:
        for key, label in _SOURCE_LABELS.items():
            count = breakdown_raw.get(key, 0)
            source_breakdown.append({
                "key": key, "label": label, "count": count,
                "width": f"{count / source_total * 100:.0f}",
            })
    earned_media_gap = bool(
        source_total
        and breakdown_raw.get("own", 0) > 0
        and (breakdown_raw.get("media", 0) + breakdown_raw.get("reference", 0)) == 0
    )

    return render(request, "ai_queries/geo_audit.html", {
        "audit": audit,
        "result": result,
        "combined_queries": combined_queries,
        "source_breakdown": source_breakdown,
        "earned_media_gap": earned_media_gap,
        "post": {
            "brand": audit.brand,
            "brand_url": audit.brand_url,
            "queries": "\n".join(audit.queries),
        },
    })


@login_required
def geo_audit_poll_view(request, pk):

    from ai_queries.models import GeoAuditResult
    audit = get_object_or_404(GeoAuditResult, id=pk, user=request.user)

    return JsonResponse({
        "status": audit.status,
        "progress": audit.progress,
        "total": len(audit.queries),
        "result": audit.result if audit.status == GeoAuditResult.STATUS_DONE else None,
        "error": audit.error if audit.status == GeoAuditResult.STATUS_ERROR else None,
    })

# --- PDF-выгрузка GEO-аудита -----------------------------------------------

def _build_geo_pdf_context(audit):
    from ai_queries.services.llm import generate_geo_summary

    result = audit.result or {}
    geo = result.get("geo") or {}
    chatgpt = result.get("chatgpt") or {}

    # ленивый кэш AI-резюме: генерим один раз, дальше берём готовое
    summary = result.get("ai_summary")
    if not summary:
        summary = generate_geo_summary(audit.brand, geo, chatgpt)
        if summary:
            result["ai_summary"] = summary
            audit.result = result
            audit.save(update_fields=["result"])

    # склейка запросов Yandex + ChatGPT (как в geo_audit_status_view)
    yandex_detail = geo.get("queries_detail", []) or []
    chatgpt_detail = chatgpt.get("queries_detail", []) or []
    chatgpt_map = {r["query"]: r for r in chatgpt_detail}
    combined_queries = []
    for r in yandex_detail:
        combined_queries.append({
            "query": r.get("query", ""),
            "yandex": r,
            "chatgpt": chatgpt_map.get(r.get("query", ""), {}),
        })

    # конкуренты → бары (ширина относительно максимума)
    competitors_raw = geo.get("competitors") or {}
    competitors = []
    if competitors_raw:
        max_count = max(competitors_raw.values())
        for domain, count in list(competitors_raw.items())[:8]:
            pct = (count / max_count * 100) if max_count else 0
            competitors.append({"domain": domain, "count": count, "width": f"{pct:.0f}"})

    # источники цитирования → бары + флаг earned media gap
    _SOURCE_LABELS = {
        "own": "Свой сайт",
        "marketplace": "Маркетплейсы",
        "media": "СМИ",
        "social": "Соцсети",
        "reference": "Справочники",
        "other": "Прочее",
    }
    breakdown_raw = geo.get("source_breakdown") or {}
    source_total = sum(breakdown_raw.values())
    source_breakdown = []
    if source_total:
        for key, label in _SOURCE_LABELS.items():
            count = breakdown_raw.get(key, 0)
            pct = count / source_total * 100
            source_breakdown.append({
                "key": key,
                "label": label,
                "count": count,
                "width": f"{pct:.0f}",
            })
    # earned media gap: свой сайт цитируется, а внешний авторитет (СМИ+справочники) — нет
    earned_media_gap = bool(
        source_total
        and breakdown_raw.get("own", 0) > 0
        and (breakdown_raw.get("media", 0) + breakdown_raw.get("reference", 0)) == 0
    )

    return {
        "audit": audit,
        "geo": geo,
        "chatgpt": chatgpt,
        "summary": summary,
        "combined_queries": combined_queries,
        "competitors": competitors,
        "source_breakdown": source_breakdown,
        "earned_media_gap": earned_media_gap,
        "query_count": len(yandex_detail),
    }


@login_required
def geo_audit_pdf_view(request, pk):
    from django.http import HttpResponse
    from django.template.loader import render_to_string
    from django.shortcuts import get_object_or_404, redirect
    from weasyprint import HTML
    from ai_queries.models import GeoAuditResult

    audit = get_object_or_404(GeoAuditResult, id=pk, user=request.user)
    if audit.status != GeoAuditResult.STATUS_DONE or not audit.result:
        return redirect("geo_audit_status", pk=pk)

    context = _build_geo_pdf_context(audit)
    html_string = render_to_string("ai_queries/geo_audit_pdf.html", context)
    pdf_bytes = HTML(
        string=html_string,
        base_url=request.build_absolute_uri("/"),
    ).write_pdf()

    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = (
        f'attachment; filename="seodyssey-geo-{audit.brand}.pdf"'
    )
    return response