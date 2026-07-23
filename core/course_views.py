from __future__ import annotations
import re
from urllib.parse import parse_qs, urlparse

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render

from core.models import Site, SiteMember
from core.views import _get_profile, _integration_flags
from course.models import (
    CourseAccess,
    CourseAccessRequest,
    Homework,
    Lesson,
    Module,
    Quiz,
    Submission,
)


def _video_embed_url(url: str) -> str:
    """
    Превращает ссылку на видео в embed-формат для iframe.
    VK Видео: vk.com/video<oid>_<id>, vkvideo.ru/..., готовый video_ext.php
    (передаётся как есть — сохраняет hash приватных видео «доступ по ссылке»).
    YouTube: watch?v=, youtu.be/, shorts/, готовый embed/.
    Неизвестный хост или пустая ссылка → "" (шаблон не покажет плеер).
    """
    if not url:
        return ""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().removeprefix("www.").removeprefix("m.")

    if host in ("vk.com", "vk.ru", "vkvideo.ru"):
        if parsed.path.startswith("/video_ext.php"):
            return url
        m = re.match(r"^/video(-?\d+)_(\d+)", parsed.path)
        if m:
            return f"https://vkvideo.ru/video_ext.php?oid={m.group(1)}&id={m.group(2)}&hd=2"
        return ""

    video_id = ""
    if host == "youtu.be":
        video_id = parsed.path.strip("/").split("/")[0]
    elif host in ("youtube.com", "m.youtube.com", "youtube-nocookie.com"):
        if parsed.path == "/watch":
            video_id = parse_qs(parsed.query).get("v", [""])[0]
        elif parsed.path.startswith(("/embed/", "/shorts/", "/live/")):
            video_id = parsed.path.split("/")[2] if len(parsed.path.split("/")) > 2 else ""

    if not video_id:
        return ""
    return f"https://www.youtube-nocookie.com/embed/{video_id}"


def _require_staff(request) -> None:
    """Курс пока скрыт от пользователей: доступен только staff."""
    if not request.user.is_staff:
        raise PermissionDenied


def _base_context(request) -> dict:
    """Контекст app-шелла — как в help_page (sites для меню + флаги интеграций)."""
    profile = _get_profile(request.user)
    flags = _integration_flags(profile)
    site_ids = list(
        SiteMember.objects.filter(user=request.user, site__is_deleted=False).values_list("site_id", flat=True)
    )
    sites = Site.objects.filter(id__in=site_ids, is_deleted=False).order_by("name")
    return {"sites": sites, **flags}


@login_required
def course_index(request):
    _require_staff(request)
    modules = Module.objects.filter(is_published=True).prefetch_related("lessons")
    modules_with_lessons = [
        (module, module.lessons.filter(is_published=True))
        for module in modules
    ]
    # Уроки без модуля (module=NULL) показываем отдельным блоком
    orphan_lessons = Lesson.objects.filter(module__isnull=True, is_published=True)

    context = {
        **_base_context(request),
        "modules_with_lessons": modules_with_lessons,
        "orphan_lessons": orphan_lessons,
        "has_access": CourseAccess.user_has_access(request.user),
    }
    return render(request, "core/course_index.html", context)


def _check_quiz_answers(questions, post_data) -> dict:
    """
    Сверяет ответы из POST с is_correct.
    Вопрос без ответа или с чужим choice_id считается отвеченным неверно.
    """
    correct = 0
    detailed = []
    for question in questions:
        raw = post_data.get(f"question_{question.id}", "")
        selected = None
        if raw.isdigit():
            selected = next(
                (c for c in question.choices.all() if c.id == int(raw)), None
            )
        is_right = bool(selected and selected.is_correct)
        correct += int(is_right)
        detailed.append({
            "question": question,
            "selected": selected,
            "is_right": is_right,
            "correct_choices": [c for c in question.choices.all() if c.is_correct],
        })
    return {
        "correct": correct,
        "total": len(questions),
        "percent": round(correct * 100 / len(questions)),
        "detailed": detailed,
    }


@login_required
def course_lesson(request, slug):
    _require_staff(request)
    lesson = get_object_or_404(
        Lesson.objects.select_related("module"), slug=slug, is_published=True
    )

    # Платный урок закрыт без активного доступа; is_free открыт всем.
    # Вместо тихого редиректа показываем paywall и собираем заявку на доступ.
    if not lesson.is_free and not CourseAccess.user_has_access(request.user):
        if request.method == "POST" and "request_access" in request.POST:
            CourseAccessRequest.objects.get_or_create(
                user=request.user, is_handled=False,
                defaults={"lesson": lesson},
            )
            return redirect(request.path)  # PRG: обновление не плодит заявки
        modules = Module.objects.filter(is_published=True).prefetch_related("lessons")
        teaser_modules = [
            (module, module.lessons.filter(is_published=True))
            for module in modules
        ]
        return render(request, "core/course_locked.html", {
            **_base_context(request),
            "lesson": lesson,
            "has_request": CourseAccessRequest.objects.filter(
                user=request.user, is_handled=False
            ).exists(),
            "teaser_modules": teaser_modules,
        })

    quiz = (
        Quiz.objects.filter(lesson=lesson)
        .prefetch_related("questions__choices")
        .first()
    )
    # Вопросы без вариантов ответа не показываем — на них нечего отвечать
    questions = [q for q in quiz.questions.all() if q.choices.all()] if quiz else []

    quiz_result = None
    if request.method == "POST" and "quiz_submit" in request.POST and questions:
        quiz_result = _check_quiz_answers(questions, request.POST)

    homework = Homework.objects.filter(lesson=lesson).first()
    hw_latest = None
    hw_can_submit = False
    if homework:
        # Meta.ordering = ["-submitted_at"], поэтому first() = последняя сдача
        hw_latest = homework.submissions.filter(user=request.user).first()
        hw_can_submit = (
            hw_latest is None
            or hw_latest.status == Submission.STATUS_NEEDS_REVISION
        )

    if request.method == "POST" and "homework_body" in request.POST:
        body = request.POST["homework_body"].strip()
        if homework and hw_can_submit and body:
            Submission.objects.create(homework=homework, user=request.user, body=body)
        # PRG: после сдачи редирект, чтобы обновление страницы не создавало дубль
        return redirect(f"{request.path}#homework")

    context = {
        **_base_context(request),
        "lesson": lesson,
        "embed_url": _video_embed_url(lesson.video_url),
        "quiz": quiz,
        "quiz_questions": questions,
        "quiz_result": quiz_result,
        "homework": homework,
        "hw_latest": hw_latest,
        "hw_can_submit": hw_can_submit,
    }
    return render(request, "core/course_lesson.html", context)
