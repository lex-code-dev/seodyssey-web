from django import forms
from django.core.exceptions import ValidationError
from urllib.parse import urlparse

from .models import Site


def normalize_domain(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        return ""

    # чтобы urlparse корректно вытащил hostname даже если ввели без схемы
    raw2 = raw if "://" in raw else "https://" + raw
    p = urlparse(raw2)
    host = p.hostname or ""

    # на всякий случай прибьём точки и пробелы по краям
    return host.strip().strip(".").lower()


class AddSiteForm(forms.ModelForm):
    restored_site = None

    class Meta:
        model = Site
        fields = ["name", "domain"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # UI classes (Tailwind)
        self.fields["name"].widget.attrs.update({
            "class": "mt-2 w-full px-4 py-3 rounded-2xl border border-slate-200 bg-slate-50 focus:outline-none focus:ring-2 focus:ring-emerald-200",
            "placeholder": "Например: 1984",
        })
        self.fields["domain"].widget.attrs.update({
            "class": "mt-2 w-full px-4 py-3 rounded-2xl border border-slate-200 bg-slate-50 focus:outline-none focus:ring-2 focus:ring-emerald-200",
            "placeholder": "например: https://19agency84.ru",
        })

    def clean_domain(self):
        domain_raw = self.cleaned_data["domain"]
        domain = normalize_domain(domain_raw)

        if not domain:
            raise ValidationError("Укажи домен (например: example.com)")

        # Ищем уже существующий сайт по НОРМАЛИЗОВАННОМУ домену
        existing = Site.objects.filter(domain=domain).first()

        # Если он удалён — пометим, что будем восстанавливать
        if existing and existing.is_deleted:
            self.restored_site = existing
            return domain

        # Если он НЕ удалён — это дубль
        if existing and not existing.is_deleted:
            raise ValidationError("Такой домен уже добавлен.")

        return domain

    def validate_unique(self):
        """
        Django ModelForm сам проверяет unique поля.
        Нам нужно пропустить эту проверку, если сайт найден и он is_deleted=True
        (мы его восстановим).
        """
        if getattr(self, "restored_site", None):
            return

        super().validate_unique()