from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponseForbidden, HttpResponse
from django.template.loader import render_to_string

from core.models import Site, SiteMember
from audits.models import AuditResult
from audits.tasks import run_site_audit


def _check_odyssey(request):
    return request.user.is_authenticated and request.user.username == "odyssey"

@login_required
def audit_list(request):
    from core.models import SiteMember, Site
    site_ids = list(
        SiteMember.objects.filter(user=request.user, site__is_deleted=False).values_list('site_id', flat=True)
    )
    audits = AuditResult.objects.filter(site_id__in=site_ids).select_related('site').order_by('-created_at')[:50]
    sites = Site.objects.filter(id__in=site_ids, is_deleted=False).order_by('domain')
    return render(request, 'audits/audit_list.html', {'audits': audits, 'sites': sites})


@login_required
def run_audit(request, site_id):

    site = get_object_or_404(Site, id=site_id, is_deleted=False)

    has_access = SiteMember.objects.filter(site=site, user=request.user).exists()
    if not has_access:
        return HttpResponseForbidden("Нет доступа к этому сайту.")

    # Создаём запись со статусом pending
    audit = AuditResult.objects.create(
        site=site,
        status=AuditResult.STATUS_PENDING,
    )

    run_site_audit.delay(site_id=site.id, triggered_by_username=request.user.username)

    return redirect("audit_result", site_id=site_id)


@login_required
def audit_result(request, site_id):

    site = get_object_or_404(Site, id=site_id, is_deleted=False)

    has_access = SiteMember.objects.filter(site=site, user=request.user).exists()
    if not has_access:
        return HttpResponseForbidden("Нет доступа к этому сайту.")

    audit = AuditResult.objects.filter(site=site).first()
    is_running = audit and audit.status in (AuditResult.STATUS_PENDING, AuditResult.STATUS_RUNNING)

    return render(request, "audits/audit_result.html", {
        "site": site,
        "audit": audit,
        "is_running": is_running,
    })

# --- PDF-выгрузка аудита ---------------------------------------------------

BLOCK_META = {
    "technical":     ("Техническое здоровье", "Доступность, отклик, сертификаты"),
    "indexability":  ("Индексируемость", "robots.txt, sitemap, мета-теги"),
    "seo":           ("SEO-основа", "Title, description, заголовки, скорость"),
    "trust":         ("Доверие и аналитика", "Счётчики, контактные данные, политики"),
    "ai_geo":        ("AI / GEO-готовность", "Распознаваемость для языковых моделей"),
    "sitemap_pages": ("Страницы из Sitemap", "HTTP-статусы, noindex, title, description, H1"),
}


def _status_of(item):
    """Достаёт status и из dict, и из объекта — не гадаем про тип checks."""
    if isinstance(item, dict):
        return item.get("status", "")
    return getattr(item, "status", "")


def _build_pdf_context(site, audit):
    results = audit.results or {}
    block_summaries = getattr(audit, "block_summaries", None) or {}

    total_ok = total_warn = total_fail = 0
    blocks = []

    for key, checks in results.items():
        ok = warn = fail = 0
        for item in checks:
            st = _status_of(item)
            if st == "ok":
                ok += 1
            elif st == "warning":
                warn += 1
            else:
                fail += 1
        total_ok += ok
        total_warn += warn
        total_fail += fail

        title, subtitle = BLOCK_META.get(key, (key, ""))
        blocks.append({
            "title": title,
            "subtitle": subtitle,
            "ok": ok,
            "warn": warn,
            "fail": fail,
            "summary": block_summaries.get(key, ""),
            "checks": checks,
        })

    total = total_ok + total_warn + total_fail
    pct = (total_ok / total * 100) if total else 0

    # геометрия бублика: окружность r=50 → 2*pi*50 ≈ 314.16
    c = 314.16
    len_ok = (total_ok / total * c) if total else 0
    len_warn = (total_warn / total * c) if total else 0
    len_fail = (total_fail / total * c) if total else 0

    return {
        "site": site,
        "audit": audit,
        "blocks": blocks,
        "total_ok": total_ok,
        "total_warn": total_warn,
        "total_fail": total_fail,
        # строки с точкой — защита от запятой в русской локали
        "health_percent": f"{pct:.1f}",
        "donut_ok": f"{len_ok:.2f}",
        "donut_warn": f"{len_warn:.2f}",
        "donut_fail": f"{len_fail:.2f}",
        "donut_warn_offset": f"{-len_ok:.2f}",
        "donut_fail_offset": f"{-(len_ok + len_warn):.2f}",
    }


@login_required
def audit_result_pdf(request, site_id):
    site = get_object_or_404(Site, id=site_id, is_deleted=False)
    has_access = SiteMember.objects.filter(site=site, user=request.user).exists()
    if not has_access:
        return HttpResponseForbidden("Нет доступа к этому сайту.")

    audit = AuditResult.objects.filter(site=site).first()
    if not audit or not audit.results:
        # анализ ещё не готов — отправляем на обычную страницу результата
        return redirect("audit_result", site_id=site_id)

    context = _build_pdf_context(site, audit)
    html_string = render_to_string("audits/audit_pdf.html", context)
    from weasyprint import HTML  # отложенный импорт: грузится только при генерации PDF
    pdf_bytes = HTML(
        string=html_string,
        base_url=request.build_absolute_uri("/"),
    ).write_pdf()

    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = (
        f'attachment; filename="seodyssey-audit-{site.domain}.pdf"'
    )
    return response