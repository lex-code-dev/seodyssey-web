from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponseForbidden

from core.models import Site, SiteMember
from audits.models import AuditResult
from audits.tasks import run_site_audit


def _check_odyssey(request):
    return request.user.is_authenticated and request.user.username == "odyssey"


@login_required
def run_audit(request, site_id):
    if not _check_odyssey(request):
        return HttpResponseForbidden("Аудит пока недоступен.")

    site = get_object_or_404(Site, id=site_id, is_deleted=False)

    has_access = SiteMember.objects.filter(site=site, user=request.user).exists()
    if not has_access:
        return HttpResponseForbidden("Нет доступа к этому сайту.")

    # Запускаем синхронно пока без Celery worker
    run_site_audit(site_id=site.id, triggered_by_username=request.user.username)

    return redirect("audit_result", site_id=site_id)


@login_required
def audit_result(request, site_id):
    if not _check_odyssey(request):
        return HttpResponseForbidden("Аудит пока недоступен.")

    site = get_object_or_404(Site, id=site_id, is_deleted=False)

    has_access = SiteMember.objects.filter(site=site, user=request.user).exists()
    if not has_access:
        return HttpResponseForbidden("Нет доступа к этому сайту.")

    audit = AuditResult.objects.filter(site=site).first()

    return render(request, "audits/audit_result.html", {
        "site": site,
        "audit": audit,
    })