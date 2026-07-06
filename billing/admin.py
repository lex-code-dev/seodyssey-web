from django.contrib import admin
from .models import Plan, Subscription, Payment


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = (
        "name", "slug", "price_monthly", "max_projects",
        "audit_frequency", "ai_limit", "is_active",
    )
    list_editable = ("price_monthly", "is_active")

@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ("user", "plan", "status", "started_at", "expires_at")
    list_filter = ("status", "plan")
    search_fields = ("user__username", "user__email")
    raw_id_fields = ("user",)

@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("user", "plan", "amount", "status", "is_processed", "created_at")
    list_filter = ("status", "is_processed", "plan")
    search_fields = ("user__username", "yookassa_id")
    readonly_fields = ("yookassa_id", "created_at", "updated_at")