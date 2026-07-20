from django.conf import settings
from django.db import models


class EmailLog(models.Model):
    """Журнал отправленных писем: аудит + дедупликация предупреждений об истечении."""

    EVENT_WELCOME = "welcome"
    EVENT_OWNER_REGISTRATION = "owner_registration"
    EVENT_OWNER_PAYMENT = "owner_payment"
    EVENT_SSL_EXPIRY = "ssl_expiry"
    EVENT_DOMAIN_EXPIRY = "domain_expiry"
    EVENT_CHOICES = [
        (EVENT_WELCOME, "Приветствие после регистрации"),
        (EVENT_OWNER_REGISTRATION, "Владельцу: новая регистрация"),
        (EVENT_OWNER_PAYMENT, "Владельцу: успешная оплата"),
        (EVENT_SSL_EXPIRY, "SSL истекает"),
        (EVENT_DOMAIN_EXPIRY, "Домен истекает"),
    ]

    STATUS_QUEUED = "queued"
    STATUS_SENT = "sent"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [
        (STATUS_QUEUED, "В очереди"),
        (STATUS_SENT, "Отправлено"),
        (STATUS_FAILED, "Ошибка"),
    ]

    event = models.CharField("Событие", max_length=32, choices=EVENT_CHOICES)
    to_email = models.EmailField("Кому")
    subject = models.CharField("Тема", max_length=255)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="Пользователь",
        null=True, blank=True, on_delete=models.SET_NULL,
        related_name="email_logs",
    )
    site = models.ForeignKey(
        "core.Site",
        verbose_name="Сайт",
        null=True, blank=True, on_delete=models.SET_NULL,
        related_name="email_logs",
    )
    # Для писем «SSL/домен истекает»: порог (30/7/3) и дата окончания,
    # по которым дедуплицируем — один порог = одно письмо на текущий срок действия.
    threshold_days = models.IntegerField("Порог, дней", null=True, blank=True)
    expires_on = models.DateField("Истекает", null=True, blank=True)

    status = models.CharField("Статус", max_length=12, choices=STATUS_CHOICES, default=STATUS_QUEUED)
    error = models.TextField("Ошибка", blank=True, default="")
    created_at = models.DateTimeField("Создано", auto_now_add=True)
    sent_at = models.DateTimeField("Отправлено", null=True, blank=True)

    class Meta:
        verbose_name = "Письмо"
        verbose_name_plural = "Журнал писем"
        indexes = [
            models.Index(fields=["site", "event", "threshold_days"]),
        ]

    def __str__(self):
        return f"{self.get_event_display()} → {self.to_email} [{self.status}]"
