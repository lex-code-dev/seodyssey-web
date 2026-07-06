from django.utils import timezone
from .models import Plan, Subscription


def get_active_plan(user):
    """
    Возвращает актуальный Plan пользователя.
    Правила:
      - нет подписки           → Free
      - подписка просрочена    → Free (expires_at в прошлом)
      - статус canceled        → Free
      - иначе                  → план подписки
    Если тариф free не заведён — вернёт None (вызывающий код должен это учесть).
    """
    free = Plan.objects.filter(slug="free").first()

    sub = Subscription.objects.filter(user=user).select_related("plan").first()
    if sub is None:
        return free

    if sub.status == Subscription.STATUS_CANCELED:
        return free

    # expires_at пустой = бессрочно (Free). Иначе проверяем срок.
    if sub.expires_at is not None and sub.expires_at < timezone.now():
        return free

    return sub.plan

def count_site_integrations(site):
    """Сколько интеграций (Яндекс) подключено у сайта. Telegram не считается."""
    count = 0
    if getattr(site, "yandex_metrica_counter_id", None):
        count += 1
    if getattr(site, "yandex_webmaster_host_id", None):
        count += 1
    return count


def can_add_integration(user, site, field_name):
    """
    Можно ли подключить интеграцию `field_name` к сайту.
    Возвращает (allowed: bool, limit: int|None).
      limit=None — без лимита.
    Замена уже подключённой интеграции того же типа всегда разрешена.
    """
    plan = get_active_plan(user)
    limit = plan.max_integrations if plan else 0

    # Без лимита (Pro/Агентство)
    if limit is None:
        return True, None

    # Замена существующей интеграции того же типа — не считаем новой
    if getattr(site, field_name, None):
        return True, limit

    # Новая интеграция: текущее число должно быть строго меньше лимита
    current = count_site_integrations(site)
    return current < limit, limit

from datetime import timedelta
from django.utils import timezone


def apply_successful_payment(payment):
    """
    Продлевает подписку по успешному платежу (вариант B).
    Идемпотентно: повторный вызов по обработанному платежу ничего не делает.
    """
    from .models import Subscription

    if payment.is_processed:
        return  # уже продлили по этому платежу

    now = timezone.now()
    sub, _ = Subscription.objects.get_or_create(
        user=payment.user,
        defaults={"plan": payment.plan, "status": Subscription.STATUS_ACTIVE},
    )

    # Вариант B: +30 дней от max(сегодня, текущий expires_at)
    base = sub.expires_at if (sub.expires_at and sub.expires_at > now) else now
    sub.plan = payment.plan
    sub.status = Subscription.STATUS_ACTIVE
    sub.expires_at = base + timedelta(days=30)
    sub.save()

    payment.is_processed = True
    payment.status = payment.STATUS_SUCCEEDED
    payment.save(update_fields=["is_processed", "status"])