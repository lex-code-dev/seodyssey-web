import secrets
from django_ckeditor_5.fields import CKEditor5Field
from django.db import models


def _gen_tg_token() -> str:
    # URL-safe токен для deep-link t.me/...?start=<token>.
    # Telegram разрешает в start-параметре только [A-Za-z0-9_-], до 64 символов — token_urlsafe это и даёт.
    return secrets.token_urlsafe(24)

def _gen_report_token() -> str:
    # Токен для гостевой ссылки на платный отчёт: /geo-check/report/<token>/
    return secrets.token_urlsafe(24)


class GeoLead(models.Model):
    CHANNEL_EMAIL = "email"
    CHANNEL_TELEGRAM = "telegram"
    CHANNEL_CHOICES = [
        (CHANNEL_EMAIL, "Email"),
        (CHANNEL_TELEGRAM, "Telegram"),
    ]

    STATUS_PENDING = "pending"
    STATUS_DONE = "done"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Ожидает"),
        (STATUS_DONE, "Отправлено"),
    ]

    url = models.URLField("Проверяемый URL", max_length=2000)
    channel = models.CharField("Канал доставки", max_length=16, choices=CHANNEL_CHOICES)
    email = models.EmailField("Email", blank=True, default="")

    # Telegram: токен генерим сразу, chat_id заполняется, когда юзер нажмёт Start
    tg_token = models.CharField("TG-токен", max_length=64, unique=True, default=_gen_tg_token, db_index=True)
    tg_chat_id = models.CharField("TG chat_id", max_length=64, blank=True, default="")

    status = models.CharField("Статус", max_length=16, choices=STATUS_CHOICES, default=STATUS_PENDING)
    result_json = models.JSONField("Результат проверки", default=list, blank=True)

    # --- Платный AI-разбор (разовая покупка) ---
    PAY_NONE = "none"
    PAY_PENDING = "pending"
    PAY_PAID = "paid"
    PAY_CHOICES = [
        (PAY_NONE, "Не оплачено"),
        (PAY_PENDING, "Ожидает оплаты"),
        (PAY_PAID, "Оплачено"),
    ]

    access_token = models.CharField(
        "Токен доступа к отчёту", max_length=64, unique=True,
        null=True, blank=True,
    )
    pay_status = models.CharField(
        "Статус оплаты", max_length=16, choices=PAY_CHOICES, default=PAY_NONE
    )
    yk_payment_id = models.CharField("YuKassa payment_id", max_length=64, blank=True, default="")
    paid_at = models.DateTimeField("Оплачено", null=True, blank=True)

    ai_report = models.JSONField("AI-разбор (кеш)", default=dict, blank=True)
    demo_item = models.JSONField(null=True, blank=True)
    recheck_count = models.PositiveIntegerField("Перепроверок сделано", default=0)

    created_at = models.DateTimeField("Создано", auto_now_add=True)
    sent_at = models.DateTimeField("Отправлено", null=True, blank=True)

    class Meta:
        verbose_name = "GEO-заявка"
        verbose_name_plural = "GEO-заявки"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.url} → {self.channel} ({self.status})"


class ServicePrice(models.Model):
    """Цена разовой услуги (напр. платный GEO-разбор). Меняется в админке."""
    code = models.SlugField(
        "Код услуги", max_length=50, unique=True,
        help_text="Технический идентификатор, напр. geo_report. Не менять без необходимости.",
    )
    title = models.CharField("Название", max_length=120)
    price = models.PositiveIntegerField("Цена, ₽")
    old_price = models.PositiveIntegerField(
        "Старая цена, ₽ (зачёркнутая)", null=True, blank=True,
        help_text="Для отображения скидки. Оставьте пустым, если скидки нет.",
    )
    is_active = models.BooleanField("Активна", default=True)
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    class Meta:
        verbose_name = "Цена услуги"
        verbose_name_plural = "Цены услуг"

    def __str__(self):
        return f"{self.title} — {self.price} ₽"

class BlogPost(models.Model):
    """Статья блога. Редактируется в админке."""
    title = models.CharField("Заголовок", max_length=200)
    slug = models.SlugField(
        "URL (slug)", max_length=200, unique=True,
        help_text="Латиницей, напр. geo-audit-dlya-magazina. Менять после публикации нежелательно.",
    )
    description = models.CharField(
        "Описание (для SEO и списка)", max_length=300, blank=True, default="",
        help_text="Короткий анонс, попадает в <meta description> и в список статей.",
    )
    body = CKEditor5Field("Текст статьи", config_name="default")
    cover = models.ImageField(
        "Обложка", upload_to="blog/covers/", null=True, blank=True,
        help_text="Необязательно. Картинка для шапки статьи.",
    )
    read_time = models.PositiveIntegerField(
        "Время чтения, мин", default=1,
        help_text="Считается автоматически при сохранении.",
    )
    is_published = models.BooleanField(
        "Опубликовано", default=False,
        help_text="Снимите галку, чтобы скрыть статью с сайта (черновик).",
    )
    created_at = models.DateTimeField("Создано", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлено", auto_now=True)
    published_at = models.DateField(
        "Дата публикации", null=True, blank=True,
        help_text="Показывается в статье и используется в sitemap. Можно задать вручную.",
    )

    class Meta:
        verbose_name = "Статья блога"
        verbose_name_plural = "Статьи блога"
        ordering = ["-published_at", "-created_at"]

    def save(self, *args, **kwargs):
        # Авторасчёт времени чтения: ~150 слов/мин по тексту без HTML-тегов.
        import re
        text = re.sub(r"<[^>]+>", " ", self.body or "")
        words = len(text.split())
        self.read_time = max(1, round(words / 150))
        super().save(*args, **kwargs)

    def __str__(self):
        return self.title


class FAQItem(models.Model):
    """Вопрос-ответ для блока FAQ статьи. Идёт в видимый FAQ и в FAQPage JSON-LD."""
    post = models.ForeignKey(
        BlogPost,
        on_delete=models.CASCADE,
        related_name="faq_items",
        verbose_name="Статья",
    )
    question = models.CharField("Вопрос", max_length=300)
    answer = models.TextField("Ответ")
    order = models.PositiveIntegerField(
        "Порядок", default=0,
        help_text="Чем меньше число, тем выше вопрос в списке.",
    )

    class Meta:
        verbose_name = "Вопрос FAQ"
        verbose_name_plural = "FAQ (вопросы)"
        ordering = ["order", "id"]

    def __str__(self):
        return self.question