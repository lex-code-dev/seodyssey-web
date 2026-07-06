from __future__ import annotations
from urllib.parse import parse_qs, urlparse

from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, render

from core.models import Site, SiteMember
from core.views import _get_profile, _integration_flags
from course.models import Lesson, Module


def _youtube_embed_url(url: str) -> str:
    """
    Превращает обычную YouTube-ссылку в embed-формат.
    Поддерживает: watch?v=, youtu.be/, shorts/, уже готовый embed/.
    Не-YouTube или пустая ссылка → "" (шаблон не покажет плеер).
    """
    if not url:
        return ""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().removeprefix("www.")

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
    }
    return render(request, "core/course_index.html", context)


@login_required
def course_lesson(request, slug):
    lesson = get_object_or_404(
        Lesson.objects.select_related("module"), slug=slug, is_published=True
    )
    context = {
        **_base_context(request),
        "lesson": lesson,
        "embed_url": _youtube_embed_url(lesson.video_url),
    }
    return render(request, "core/course_lesson.html", context)
