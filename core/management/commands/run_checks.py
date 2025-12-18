from django.core.management.base import BaseCommand
from core.models import Site, CheckRun
import requests


class Command(BaseCommand):
    help = "Run site availability checks"

    def handle(self, *args, **options):
        sites = Site.objects.filter(is_active=True)

        if not sites:
            self.stdout.write("No active sites found.")
            return

        for site in sites:
            try:
                response = requests.get(site.url, timeout=10)
                status = response.status_code
                is_up = 200 <= status < 400

                # 1) сохраняем в базу
                CheckRun.objects.create(
                    site=site,
                    is_up=is_up,
                    http_status=status,
                    error=""
                )

                # 2) выводим в терминал
                label = "OK" if is_up else "WARN"
                self.stdout.write(f"[{label}] {site.name} — {site.url} (HTTP {status})")

            except Exception as e:
                # сохраняем ошибку как неуспешную проверку
                CheckRun.objects.create(
                    site=site,
                    is_up=False,
                    http_status=None,
                    error=str(e)
                )

                self.stdout.write(f"[ERROR] {site.name} — {site.url} ({e})")
