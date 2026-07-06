from django import forms
from core.models import UserProfile


class UserProfileForm(forms.ModelForm):
    class Meta:
        model = UserProfile
        fields = ["telegram_chat_id"]
        widgets = {
            "telegram_chat_id": forms.TextInput(
                attrs={
                    "style": "width:200px;padding:8px 12px;border-radius:10px;border:0.5px solid #e5e5e3;background:#f7f7f5;font-size:13px;",
                    "placeholder": "например: 123456789",
                }
            ),
        }