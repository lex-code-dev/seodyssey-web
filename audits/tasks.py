from celery import shared_task
from django.utils import timezone


@shared_task
def run_site_audit(site_id: int, triggered_by_username: str = None):
    from core.models import Site, SiteMember
    from audits.models import AuditResult
    from audits.runner import run_audit
    from audits.ai_summary import generate_summary

    try:
        site = Site.objects.get(id=site_id, is_deleted=False)
    except Site.DoesNotExist:
        return {"error": f"Site {site_id} not found"}

    has_access = SiteMember.objects.filter(
        site=site,
        user__username=triggered_by_username
    ).exists()

    if not has_access:
        return {"skipped": True, "reason": "site does not belong to odyssey"}

    # Берём последнюю запись со статусом pending
    audit = AuditResult.objects.filter(
        site=site,
        status=AuditResult.STATUS_PENDING
    ).first()

    if not audit:
        audit = AuditResult.objects.create(site=site)

    audit.status = AuditResult.STATUS_RUNNING
    audit.save(update_fields=["status"])

    try:
        results = run_audit(site.domain)

        # Генерируем AI-резюме (общее + по блокам)
        summary, block_summaries = generate_summary(site.domain, results)

        audit.results = results
        audit.summary = summary
        audit.block_summaries = block_summaries
        audit.status = AuditResult.STATUS_DONE
        audit.finished_at = timezone.now()
        audit.save()

        return {"audit_id": audit.id, "status": "done"}

    except Exception as e:
        audit.status = AuditResult.STATUS_ERROR
        audit.finished_at = timezone.now()
        audit.save()

        return {"audit_id": audit.id, "status": "error", "error": str(e)}