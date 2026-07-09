from .models import Module


def course_nav(request):
    """
    Флаг для навигации: показывать пункт «Курс» в меню только когда есть
    хотя бы один опубликованный модуль — чтобы пустой курс не маячил.
    """
    if not request.user.is_authenticated:
        return {}
    return {"course_available": Module.objects.filter(is_published=True).exists()}
