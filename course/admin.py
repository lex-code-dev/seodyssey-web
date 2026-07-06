from django.contrib import admin

from .models import (
    Choice,
    CourseAccess,
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


@admin.register(Homework)
class HomeworkAdmin(admin.ModelAdmin):
    list_display = ("__str__", "lesson", "updated_at")
    list_filter = ("lesson__module",)
    search_fields = ("text",)


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
