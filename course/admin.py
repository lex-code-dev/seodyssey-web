from django.contrib import admin
from django.db.models import Count, Q
from django.utils import timezone

from .models import (
    Choice,
    CourseAccess,
    CourseAccessRequest,
    Homework,
    Lesson,
    Module,
    Question,
    Quiz,
    Submission,
)


class LessonInline(admin.TabularInline):
    model = Lesson
    fields = ("order", "title", "slug", "is_free", "is_published")
    prepopulated_fields = {"slug": ("title",)}
    extra = 0
    show_change_link = True


@admin.register(Module)
class ModuleAdmin(admin.ModelAdmin):
    list_display = ("order", "title", "slug", "is_published")
    list_editable = ("is_published",)
    prepopulated_fields = {"slug": ("title",)}
    ordering = ("order",)
    inlines = [LessonInline]


@admin.register(Lesson)
class LessonAdmin(admin.ModelAdmin):
    list_display = ("order", "title", "module", "is_free", "is_published")
    list_editable = ("is_free", "is_published")
    list_filter = ("module", "is_free", "is_published")
    search_fields = ("title", "body")
    prepopulated_fields = {"slug": ("title",)}
    ordering = ("module__order", "order")


class QuestionInline(admin.TabularInline):
    model = Question
    fields = ("order", "text")
    extra = 0
    show_change_link = True


@admin.register(Quiz)
class QuizAdmin(admin.ModelAdmin):
    list_display = ("__str__", "lesson", "created_at")
    list_filter = ("lesson__module",)
    inlines = [QuestionInline]


class ChoiceInline(admin.TabularInline):
    model = Choice
    fields = ("text", "is_correct")
    extra = 0


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = ("__str__", "quiz", "order")
    list_filter = ("quiz",)
    ordering = ("quiz", "order")
    inlines = [ChoiceInline]


class SubmissionInline(admin.TabularInline):
    model = Submission
    fields = ("user", "status", "submitted_at", "body")
    readonly_fields = ("user", "submitted_at", "body")
    extra = 0
    can_delete = False
    show_change_link = True

    def has_add_permission(self, request, obj=None):
        return False  # сдачи создают ученики на сайте, не админ


@admin.register(Homework)
class HomeworkAdmin(admin.ModelAdmin):
    list_display = ("__str__", "lesson", "pending_count", "accepted_count", "updated_at")
    list_filter = ("lesson__module",)
    search_fields = ("text",)
    inlines = [SubmissionInline]

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(
            _pending=Count("submissions", filter=Q(submissions__status=Submission.STATUS_SUBMITTED)),
            _accepted=Count("submissions", filter=Q(submissions__status=Submission.STATUS_ACCEPTED)),
        )

    @admin.display(description="Ждут проверки", ordering="_pending")
    def pending_count(self, obj):
        return obj._pending

    @admin.display(description="Принято", ordering="_accepted")
    def accepted_count(self, obj):
        return obj._accepted


@admin.register(Submission)
class SubmissionAdmin(admin.ModelAdmin):
    list_display = ("user", "homework", "status", "submitted_at")
    list_editable = ("status",)
    list_filter = ("status", "homework__lesson__module")
    search_fields = ("user__username", "user__email", "body")
    readonly_fields = ("submitted_at", "updated_at")
    ordering = ("-submitted_at",)


@admin.register(CourseAccess)
class CourseAccessAdmin(admin.ModelAdmin):
    list_display = ("user", "paid_at", "is_active", "payment")
    list_editable = ("is_active",)
    list_filter = ("is_active",)
    search_fields = ("user__username", "user__email")
    ordering = ("-paid_at",)


@admin.register(CourseAccessRequest)
class CourseAccessRequestAdmin(admin.ModelAdmin):
    list_display = ("user", "lesson", "is_handled", "created_at")
    list_filter = ("is_handled",)
    search_fields = ("user__username", "user__email")
    readonly_fields = ("created_at", "updated_at")
    ordering = ("is_handled", "-created_at")
    actions = ["grant_access"]

    @admin.action(description="Выдать доступ к курсу и отметить обработанной")
    def grant_access(self, request, queryset):
        granted = 0
        for req in queryset:
            _, created = CourseAccess.objects.get_or_create(
                user=req.user,
                defaults={"paid_at": timezone.now(), "is_active": True},
            )
            granted += int(created)
            req.is_handled = True
            req.save(update_fields=["is_handled", "updated_at"])
        self.message_user(
            request,
            f"Обработано заявок: {queryset.count()}. Новых доступов выдано: {granted}.",
        )
