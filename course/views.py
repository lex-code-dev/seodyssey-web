from django.shortcuts import render, get_object_or_404
from django.http import Http404

from .models import Lesson


def _visible_qs(request):
    # Пока пилим скрытно: staff видит всё, остальные — только published.
    # Но на этапе разработки мы ещё и весь /course/ прячем в middleware,
    # так что реально сюда попадаешь только ты.
    if request.user.is_staff:
        return Lesson.objects.all()
    return Lesson.objects.filter(is_published=True)


def lesson_list(request):
    lessons = _visible_qs(request)
    return render(request, "course/lesson_list.html", {"lessons": lessons})


def lesson_detail(request, slug):
    lesson = get_object_or_404(_visible_qs(request), slug=slug)
    return render(request, "course/lesson_detail.html", {"lesson": lesson})