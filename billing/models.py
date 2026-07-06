from django.db import models
from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver


class Plan(models.Model):
    FREQ_MANUAL = "manual"
    FREQ_WEEKLY = "weekly"
    FREQ_DAILY = "daily"
    FREQ_CHOICES = [
        (FREQ_MANUAL, "Вручную"),
        (FREQ_WEEKLY, "Раз в неделю"),
        (FREQ_DAILY, "Ежедневно"),
    ]

    name = models.CharField("Название", max_length=50)
    slug = models.SlugField("Код", unique=True)  # free / start / pro / agency
    price_monthly = models.PositiveIntegerField("Цена в месяц, ₽", default=0)
    sort_order = models.PositiveIntegerField("Порядок", default=0)
    is_active = models.BooleanField("Активен", default=True)

    # Лимиты
    max_projects = models.PositiveIntegerField("Макс. проектов", default=1)
    audit_frequency = models.CharField(
        "Частота автоаудита", max_length=10,
        choices=FREQ_CHOICES, default=FREQ_MANUAL,
    )
    ai_limit = models.PositiveIntegerField("AI-рекомендаций в месяц", default=0)
    max_integrations = models.PositiveIntegerField(
        "Макс. интеграций", null=True, blank=True,
        help_text="Пусто = без лимита",
    )
    history_days = models.PositiveIntegerField(
        "История, дней", null=True, blank=True,
        help_text="Пусто = без лимита",
    )
    telegram_alerts = models.BooleanField("Telegram-алерты", default=False)

    class Meta:
        verbose_name = "Тариф"
        verbose_name_plural = "Тарифы"
        ordering = ["sort_order"]

    def __str__(self):
        return f"{self.name} ({self.price_monthly} ₽/мес)"

class Subscription(models.Model):
    STATUS_TRIAL = "trial"
    STATUS_ACTIVE = "active"
    STATUS_PAST_DUE = "past_due"
    STATUS_CANCELED = "canceled"
    STATUS_CHOICES = [
        (STATUS_TRIAL, "Триал"),
        (STATUS_ACTIVE, "Активна"),
        (STATUS_PAST_DUE, "Просрочена"),
        (STATUS_CANCELED, "Отменена"),
    ]

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="subscription",
        verbose_name="Пользователь",
    )
    plan = models.ForeignKey(
        Plan,
        on_delete=models.PROTECT,
        related_name="subscriptions",
        verbose_name="Тариф",
    )
    status = models.CharField(
        "Статус", max_length=10,
        choices=STATUS_CHOICES, default=STATUS_ACTIVE,
    )
    started_at = models.DateTimeField("Начало", auto_now_add=True)
    expires_at = models.DateTimeField(
        "Истекает", null=True, blank=True,
        help_text="Пусто = бессрочно (например, Free)",
    )
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    class Meta:
        verbose_name = "Подписка"
        verbose_name_plural = "Подписки"

    def __str__(self):
        return f"{self.user} — {self.plan.name} ({self.status})"

@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_free_subscription(sender, instance, created, **kwargs):
    if not created:
        return
    free_plan = Plan.objects.filter(slug="free").first()
    if free_plan is None:
        return  # тариф free не заведён — молча выходим, регистрацию не ломаем
    Subscription.objects.get_or_create(
        user=instance,
        defaults={
            "plan": free_plan,
            "status": Subscription.STATUS_ACTIVE,
            "expires_at": None,  # Free бессрочный
        },
    )

class Payment(models.Model):
    STATUS_PENDING = "pending"
    STATUS_SUCCEEDED = "succeeded"
    STATUS_CANCELED = "canceled"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Ожидает оплаты"),
        (STATUS_SUCCEEDED, "Оплачен"),
        (STATUS_CANCELED, "Отменён"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="payments",
        verbose_name="Пользователь",
    )
    plan = models.ForeignKey(
        Plan,
        on_delete=models.PROTECT,
        related_name="payments",
        verbose_name="Тариф",
    )
    yookassa_id = models.CharField(
        "ID платежа в ЮKassa", max_length=64, unique=True, db_index=True
    )
    amount = models.DecimalField("Сумма, ₽", max_digits=10, decimal_places=2)
    status = models.CharField(
        "Статус", max_length=12, choices=STATUS_CHOICES, default=STATUS_PENDING
    )
    is_processed = models.BooleanField(
        "Подписка продлена", default=False,
        help_text="Защита от повторного продления по одному платежу",
    )
    created_at = models.DateTimeField("Создан", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлён", auto_now=True)

    class Meta:
        verbose_name = "Платёж"
        verbose_name_plural = "Платежи"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user} — {self.plan.name} — {self.amount}₽ ({self.status})"