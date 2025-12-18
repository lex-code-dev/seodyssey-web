from django.contrib import admin
from .models import Site, CheckRun


class CheckRunInline(admin.TabularInline):
    model = CheckRun
    extra = 0
    readonly_fields = ("created_at", "is_up", "http_status", "error")


@admin.register(Site)
class SiteAdmin(admin.ModelAdmin):
    list_display = ("name", "url", "is_active", "created_at")
    inlines = [CheckRunInline]


@admin.register(CheckRun)
class CheckRunAdmin(admin.ModelAdmin):
    list_display = ("site", "created_at", "is_up", "http_status")
    list_filter = ("is_up",)
    ordering = ("-created_at",)
