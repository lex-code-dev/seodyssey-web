from django.contrib import admin
from .models import Site, SiteMember, CheckRun


@admin.register(Site)
class SiteAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "domain", "is_active", "created_at")
    search_fields = ("name", "domain")


@admin.register(SiteMember)
class SiteMemberAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "site", "role")
    list_filter = ("role",)


@admin.register(CheckRun)
class CheckRunAdmin(admin.ModelAdmin):
    list_display = ("id", "site", "status", "created_at")
    list_filter = ("status",)
