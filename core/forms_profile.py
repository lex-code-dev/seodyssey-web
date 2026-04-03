from django import forms
from core.models import UserProfile


class UserProfileForm(forms.ModelForm):
    class Meta:
        model = UserProfile
        fields = ["telegram_enabled", "telegram_chat_id"]
        widgets = {
            "telegram_enabled": forms.CheckboxInput(attrs={"class": "mt-1 h-4 w-4"}),
            "telegram_chat_id": forms.TextInput(
                attrs={
                    "class": "mt-2 w-full px-4 py-3 rounded-2xl border border-slate-200 bg-slate-50 focus:outline-none focus:ring-2 focus:ring-emerald-200",
                    "placeholder": "например: 288879225",
                }
            ),
        }
