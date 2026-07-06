from django import forms
from django.core.exceptions import ValidationError
from urllib.parse import urlparse
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User

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

        # Домен должен содержать точку и иметь TLD минимум 2 символа
        parts = domain.split(".")
        if len(parts) < 2 or len(parts[-1]) < 2:
            raise ValidationError("Введи полный домен с зоной (например: example.com или site.ru)")

        # Домен не должен быть просто числом или IP без зоны
        if all(p.isdigit() for p in parts):
            raise ValidationError("Введи доменное имя, а не IP-адрес")

        self._normalized_domain = domain
        return domain

    def validate_unique(self):
        # убираем стандартную глобальную проверку уникальности domain
        pass

class SignUpForm(UserCreationForm):
    email = forms.EmailField(required=True)
    # honeypot: скрытое поле, люди его не видят, боты заполняют
    website = forms.CharField(required=False, widget=forms.HiddenInput)

    class Meta:
        model = User
        fields = ("username", "email", "password1", "password2")

    def clean_website(self):
        if self.cleaned_data.get("website"):
            raise forms.ValidationError("Spam detected.")
        return self.cleaned_data["website"]