from django.utils import timezone
from core.models import AIUsage
from billing.services import get_active_plan

UNLIMITED_USERS = {'odyssey'}


def check_and_spend_ai_limit(user) -> tuple[bool, int, int]:
    """
    Проверяет лимит и списывает 1 AI-проверку.
    Возвращает (ok, used, limit):
      ok    — можно ли выполнить запрос
      used  — сколько использовано ПОСЛЕ списания
      limit — общий лимит (0 = безлимит)
    """
    if user.username in UNLIMITED_USERS:
        return True, 0, 0

    # Лимит берём из активного тарифа пользователя (с учётом просрочки → Free)
    plan = get_active_plan(user)
    limit = plan.ai_limit if plan else 0

    month = timezone.now().strftime('%Y-%m')
    usage, _ = AIUsage.objects.get_or_create(user=user, month=month)

    if usage.used >= limit:
        return False, usage.used, limit

    usage.used += 1
    usage.save(update_fields=['used'])
    return True, usage.used, limit

def check_ai_limit(user) -> tuple[bool, int, int]:
    """
    Только ПРОВЕРЯЕТ лимит, ничего не списывает.
    Нужна, чтобы отклонить дорогой запрос (GEO-аудит) ДО вызова внешнего API.
    Возвращает (ok, used, limit).
    """
    if user.username in UNLIMITED_USERS:
        return True, 0, 0

    plan = get_active_plan(user)
    limit = plan.ai_limit if plan else 0

    month = timezone.now().strftime('%Y-%m')
    usage, _ = AIUsage.objects.get_or_create(user=user, month=month)

    return usage.used < limit, usage.used, limit