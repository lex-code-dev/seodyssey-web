from django.contrib import admin

from .models import EmailLog


@admin.register(EmailLog)
class EmailLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "event", "to_email", "subject", "status", "site", "threshold_days")
    list_filter = ("event", "status")
    search_fields = ("to_email", "subject")
    readonly_fields = [f.name for f in EmailLog._meta.fields]
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False
