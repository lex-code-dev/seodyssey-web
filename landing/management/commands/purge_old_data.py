"""
Удаление персональных данных, которые больше не нужны.

GeoLead хранит email и адрес страницы постороннего человека — он оставил их
ради одного отчёта и аккаунта у нас не заводил. UserActivity — это полный
след переходов по кабинету. И то, и другое копилось бессрочно.

Запускать по cron раз в сутки:
    manage.py purge_old_data
"""

from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta

# Заявка живёт полгода: ссылка на отчёт всё это время рабочая, дальше данные
# нужны только нам, а не человеку.
GEO_LEAD_DAYS = 180
# След активности нужен для аналитики (DAU/WAU/retention считаются за 8 недель).
USER_ACTIVITY_DAYS = 90


class Command(BaseCommand):
    help = "Удаляет устаревшие GEO-заявки и записи активности"

    def add_arguments(self, parser):
        parser.add_argument(
            "--geo-days", type=int, default=GEO_LEAD_DAYS,
            help=f"Возраст GEO-заявок в днях (по умолчанию {GEO_LEAD_DAYS})",
        )
        parser.add_argument(
            "--activity-days", type=int, default=USER_ACTIVITY_DAYS,
            help=f"Возраст записей активности в днях (по умолчанию {USER_ACTIVITY_DAYS})",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Только посчитать, ничего не удалять",
        )

    def handle(self, *args, **options):
        from core.models import UserActivity
        from landing.models import GeoLead

        dry_run = options["dry_run"]
        now = timezone.now()

        targets = [
            ("GEO-заявки", GeoLead.objects.filter(
                created_at__lt=now - timedelta(days=options["geo_days"])
            )),
            ("записи активности", UserActivity.objects.filter(
                created_at__lt=now - timedelta(days=options["activity_days"])
            )),
        ]

        for label, qs in targets:
            count = qs.count()
            if dry_run:
                self.stdout.write(f"[dry-run] {label}: под удаление {count}")
                continue
            if count:
                qs.delete()
            self.stdout.write(f"{label}: удалено {count}")
