import logging

from celery import shared_task
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.utils import timezone

logger = logging.getLogger(__name__)

RETRY_BASE_DELAY_SEC = 60


def deliver_email(log_id: int, subject: str, text_body: str, html_body: str, to_email: str) -> None:
    """Синхронная отправка одного письма с обновлением EmailLog. Бросает исключение при ошибке."""
    from .models import EmailLog

    msg = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[to_email],
    )
    if html_body:
        msg.attach_alternative(html_body, "text/html")
    msg.send()

    EmailLog.objects.filter(pk=log_id).update(
        status=EmailLog.STATUS_SENT, sent_at=timezone.now(), error=""
    )


def mark_email_failed(log_id: int, exc: Exception) -> None:
    from .models import EmailLog

    EmailLog.objects.filter(pk=log_id).update(
        status=EmailLog.STATUS_FAILED, error=str(exc)[:2000]
    )
    logger.error("Email log_id=%s failed: %s", log_id, exc)


@shared_task(bind=True, max_retries=3)
def send_email_task(self, log_id: int, subject: str, text_body: str, html_body: str, to_email: str):
    try:
        deliver_email(log_id, subject, text_body, html_body, to_email)
    except Exception as exc:
        if self.request.retries >= self.max_retries:
            mark_email_failed(log_id, exc)
            return
        raise self.retry(exc=exc, countdown=RETRY_BASE_DELAY_SEC * (2 ** self.request.retries))
