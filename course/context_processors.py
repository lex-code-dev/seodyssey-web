from .models import Module


def course_nav(request):
    """
    Флаг для навигации: пункт «Курс» видят только staff — курс ещё скрыт от
    пользователей. Плюс прежнее условие: хотя бы один опубликованный модуль,
    чтобы пустой курс не маячил.
    """
    if not request.user.is_authenticated or not request.user.is_staff:
        return {}
    return {"course_available": Module.objects.filter(is_published=True).exists()}
