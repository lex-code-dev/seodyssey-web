from django.contrib import admin
from django.db.models import Q
from .models import Site, SiteMember, CheckRun, IssueSolution


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


@admin.register(IssueSolution)
class IssueSolutionAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "title",
        "check_key",
        "issue_code",
        "severity",
        "priority",
        "is_active",
        "updated_at",
    )
    list_filter = (
        "check_key",
        "severity",
        "is_active",
    )
    search_fields = (
        "title",
        "check_key",
        "issue_code",
        "short_summary",
    )
    ordering = ("priority", "id")
    list_editable = ("priority", "is_active")
    readonly_fields = ("created_at", "updated_at")

    fieldsets = (
        ("Матчинг", {
            "fields": (
                "check_key",
                "issue_code",
                "severity",
                "match_rules",
                "priority",
                "is_active",
            )
        }),
        ("Контент решения", {
            "fields": (
                "title",
                "short_summary",
                "steps",
                "links",
            )
        }),
        ("Служебное", {
            "fields": (
                "created_at",
                "updated_at",
            )
        }),
    )

    def get_search_results(self, request, queryset, search_term):
        """
        Оставляем стандартный поиск, но добавляем удобный поиск по точному check_key/issue_code.
        """
        queryset, use_distinct = super().get_search_results(request, queryset, search_term)

        if search_term:
            queryset |= self.model.objects.filter(
                Q(check_key__iexact=search_term) |
                Q(issue_code__iexact=search_term)
            )

        return queryset, use_distinct