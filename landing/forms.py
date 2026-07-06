from django import forms
from landing.security import validate_public_url


class GeoCheckForm(forms.Form):
    url = forms.URLField(max_length=2000, error_messages={
        "required": "Укажите адрес страницы.",
        "invalid": "Похоже, это не корректный URL.",
    })
    # honeypot: скрытое поле, люди его не видят, боты заполняют
    website = forms.CharField(required=False, widget=forms.HiddenInput)

    def clean_website(self):
        if self.cleaned_data.get("website"):
            raise forms.ValidationError("Spam detected.")
        return self.cleaned_data["website"]

    def clean_url(self):
        url = self.cleaned_data["url"]
        ok, error = validate_public_url(url)
        if not ok:
            raise forms.ValidationError(error)
        return url