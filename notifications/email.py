"""Транзакционные письма SEOdyssey.

Точка входа — функции send_* / notify_*. Каждая отправка фиксируется в EmailLog.
Отправка по умолчанию асинхронная (Celery, 3 повтора с растущей задержкой);
если брокер недоступен или EMAIL_ASYNC=False — синхронно, ошибки не
пробрасываются наружу, чтобы не ломать регистрацию/вебхуки/проверки.
"""

import logging
from datetime import timedelta

from django.conf import settings
from django.template.loader import render_to_string
from django.utils import timezone

from .models import EmailLog
from .tasks import deliver_email, mark_email_failed, send_email_task

logger = logging.getLogger(__name__)

# Пороги предупреждений «SSL/домен скоро истечёт», в днях
EXPIRY_THRESHOLDS = [3, 7, 30]
# Допуск на дрейф даты окончания (округление days_left) при дедупликации
EXPIRY_DATE_TOLERANCE_DAYS = 2


def _dispatch(*, event, to_email, subject, template_base, context,
              user=None, site=None, threshold_days=None, expires_on=None) -> EmailLog | None:
    if not to_email:
        return None

    ctx = {"site_url": settings.SITE_URL, **context}
    html_body = render_to_string(f"email/{template_base}.html", ctx)
    text_body = render_to_string(f"email/{template_base}.txt", ctx)

    log = EmailLog.objects.create(
        event=event, to_email=to_email, subject=subject,
        user=user, site=site, threshold_days=threshold_days, expires_on=expires_on,
    )

    if getattr(settings, "EMAIL_ASYNC", True):
        try:
            send_email_task.delay(log.id, subject, text_body, html_body, to_email)
            return log
        except Exception:
            logger.exception("Celery недоступен, шлю письмо синхронно (log_id=%s)", log.id)

    try:
        deliver_email(log.id, subject, text_body, html_body, to_email)
    except Exception as exc:
        mark_email_failed(log.id, exc)
    return log


# ---------- Пользователю ----------

def send_welcome_email(user) -> None:
    _dispatch(
        event=EmailLog.EVENT_WELCOME,
        to_email=user.email,
        subject="Добро пожаловать в SEOdyssey",
        template_base="welcome",
        context={"username": user.username},
        user=user,
    )


def send_expiry_warnings(site, checks: dict) -> int:
    """Письма участникам сайта о скором истечении SSL/домена.

    Порог — наименьший из EXPIRY_THRESHOLDS, который уже пересечён.
    Дедупликация: по (site, event, threshold) с той же датой окончания
    (±EXPIRY_DATE_TOLERANCE_DAYS) повторно не шлём; после продления дата
    сдвигается — и цикл предупреждений начинается заново.
    Возвращает число отправленных писем.
    """
    kinds = [
        ("ssl", EmailLog.EVENT_SSL_EXPIRY, "SSL-сертификат"),
        ("domain", EmailLog.EVENT_DOMAIN_EXPIRY, "домен"),
    ]
    sent_count = 0

    for check_key, event, what in kinds:
        days_left = (checks.get(check_key) or {}).get("expires_in_days")
        if days_left is None or days_left < 0:
            continue

        threshold = min((t for t in EXPIRY_THRESHOLDS if days_left <= t), default=None)
        if threshold is None:
            continue

        expires_on = timezone.localdate() + timedelta(days=days_left)
        tolerance = timedelta(days=EXPIRY_DATE_TOLERANCE_DAYS)
        already = EmailLog.objects.filter(
            site=site, event=event, threshold_days=threshold,
            status__in=[EmailLog.STATUS_QUEUED, EmailLog.STATUS_SENT],
            expires_on__gte=expires_on - tolerance,
            expires_on__lte=expires_on + tolerance,
        ).exists()
        if already:
            continue

        recipients = {
            m.user.email
            for m in site.members.select_related("user").all()
            if m.user.email
        }
        for to_email in sorted(recipients):
            log = _dispatch(
                event=event,
                to_email=to_email,
                subject=f"{site.domain}: {what} истекает через {days_left} дн.",
                template_base="expiry_warning",
                context={
                    "site": site,
                    "what": what,
                    "check_key": check_key,
                    "days_left": days_left,
                    "expires_on": expires_on,
                },
                site=site,
                threshold_days=threshold,
                expires_on=expires_on,
            )
            if log is not None:
                sent_count += 1

    return sent_count


# ---------- Владельцу сервиса ----------

def notify_owner_new_user(user) -> None:
    _dispatch(
        event=EmailLog.EVENT_OWNER_REGISTRATION,
        to_email=settings.OWNER_NOTIFICATION_EMAIL,
        subject=f"SEOdyssey: новая регистрация — {user.username}",
        template_base="owner_registration",
        context={"reg_user": user},
        user=user,
    )


def notify_owner_payment(payment) -> None:
    _dispatch(
        event=EmailLog.EVENT_OWNER_PAYMENT,
        to_email=settings.OWNER_NOTIFICATION_EMAIL,
        subject=f"SEOdyssey: оплата {payment.amount} ₽ — {payment.user.username}",
        template_base="owner_payment",
        context={"payment": payment},
        user=payment.user,
    )
