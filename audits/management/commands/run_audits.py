from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import Site, SiteMember
from audits.models import AuditResult
from audits.tasks import run_site_audit
from billing.services import get_active_plan
from billing.models import Plan

# Сколько дней должно пройти с последнего аудита для каждой частоты
FREQUENCY_DAYS = {
    Plan.FREQ_WEEKLY: 7,
    Plan.FREQ_DAILY: 1,
}


class Command(BaseCommand):
    help = "Запускает автоаудиты по сайтам в соответствии с частотой из тарифа"

    def handle(self, *args, **options):
        now = timezone.now()
        launched = 0
        skipped = 0

        for site in Site.objects.filter(is_deleted=False):
            # 1. Владелец сайта
            owner_member = SiteMember.objects.filter(
                site=site, role=SiteMember.ROLE_OWNER
            ).select_related("user").first()
            if owner_member is None:
                skipped += 1
                continue
            owner = owner_member.user

            # 2. Частота из активного тарифа владельца
            plan = get_active_plan(owner)
            frequency = plan.audit_frequency if plan else Plan.FREQ_MANUAL
            interval_days = FREQUENCY_DAYS.get(frequency)
            if interval_days is None:
                # manual или неизвестная частота — автоаудит не запускаем
                skipped += 1
                continue

            # 3. Защита от дублей: уже есть незавершённый аудит
            active = AuditResult.objects.filter(
                site=site,
                status__in=[AuditResult.STATUS_PENDING, AuditResult.STATUS_RUNNING],
            ).exists()
            if active:
                skipped += 1
                continue

            # 4. Пора ли запускать (прошло >= interval_days с последнего DONE)
            last_done = AuditResult.objects.filter(
                site=site, status=AuditResult.STATUS_DONE
            ).order_by("-created_at").first()
            if last_done and (now - last_done.created_at) < timedelta(days=interval_days):
                skipped += 1
                continue

            # 5. Запускаем
            AuditResult.objects.create(site=site, status=AuditResult.STATUS_PENDING)
            run_site_audit.delay(site_id=site.id, triggered_by_username=owner.username)
            launched += 1
            self.stdout.write(f"  → запущен аудит: {site.domain} ({frequency})")

        self.stdout.write(self.style.SUCCESS(
            f"Готово. Запущено: {launched}, пропущено: {skipped}"
        ))