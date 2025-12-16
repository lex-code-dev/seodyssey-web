from django.core.management.base import BaseCommand
from core.models import Site
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

                if 200 <= status < 400:
                    self.stdout.write(
                        f"[OK] {site.name} — {site.url} (HTTP {status})"
                    )
                else:
                    self.stdout.write(
                        f"[WARN] {site.name} — {site.url} (HTTP {status})"
                    )

            except Exception as e:
                self.stdout.write(
                    f"[ERROR] {site.name} — {site.url} ({e})"
                )
