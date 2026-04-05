from django.conf import settings
from django.db import models
from django.utils import timezone
from urllib.parse import urlparse


def normalize_domain(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        return ""

    raw2 = raw if "://" in raw else "https://" + raw
    host = urlparse(raw2).hostname or ""
    return host.strip().lower()


class Site(models.Model):
    name = models.CharField("Название", max_length=255)
    domain = models.CharField("Домен", max_length=255, unique=True)
    is_active = models.BooleanField("Активен", default=True)
    manual_traffic_week = models.IntegerField("Трафик за неделю (ручной)", null=True, blank=True)
    manual_indexed_pages = models.IntegerField("Страниц в индексе (ручной)", null=True, blank=True)
    yandex_metrica_counter_id = models.BigIntegerField(blank=True, null=True)
    yandex_webmaster_host_id = models.CharField(max_length=128, blank=True, null=True)
    is_deleted = models.BooleanField("Удалён", default=False)
    deleted_at = models.DateTimeField("Удалён", null=True, blank=True)

    created_at = models.DateTimeField("Создан", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлён", auto_now=True)

    def soft_delete(self):
        if not self.is_deleted:
            self.is_deleted = True
            self.deleted_at = timezone.now()
            self.save(update_fields=["is_deleted", "deleted_at"])

    def restore(self):
        if self.is_deleted:
            self.is_deleted = False
            self.deleted_at = None
            self.save(update_fields=["is_deleted", "deleted_at"])
    def __str__(self):
        return f"{self.name} ({self.domain})"

    def save(self, *args, **kwargs):
        self.domain = normalize_domain(self.domain)
        super().save(*args, **kwargs)


class SiteMember(models.Model):
    """Связка: какой пользователь имеет доступ к какому сайту."""
    ROLE_OWNER = "owner"
    ROLE_VIEWER = "viewer"

    ROLE_CHOICES = (
        (ROLE_OWNER, "Владелец"),
        (ROLE_VIEWER, "Наблюдатель"),
    )

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    site = models.ForeignKey(Site, on_delete=models.CASCADE, related_name="members")
    role = models.CharField(max_length=16, choices=ROLE_CHOICES, default=ROLE_OWNER)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("user", "site")

    def __str__(self):
        return f"{self.user} -> {self.site} ({self.role})"


class CheckRun(models.Model):
    """Одна попытка проверки сайта (пока заглушка, потом будет реальная интеграция)."""
    STATUS_QUEUED = "queued"
    STATUS_RUNNING = "running"
    STATUS_OK = "ok"
    STATUS_FAIL = "fail"

    STATUS_CHOICES = (
        (STATUS_QUEUED, "В очереди"),
        (STATUS_RUNNING, "В процессе"),
        (STATUS_OK, "Успех"),
        (STATUS_FAIL, "Ошибка"),
    )

    site = models.ForeignKey(Site, on_delete=models.CASCADE, related_name="checks")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )

    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_QUEUED)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    # Результаты “на будущее”: сейчас будем класть туда простые штуки.
    result = models.JSONField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self):
        return f"Check #{self.id} for {self.site.domain} ({self.status})"

class UserProfile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
    )

    # Интеграции (пока флаги/поля-заготовки)
    yandex_connected = models.BooleanField("Яндекс подключен", default=False)

    telegram_enabled = models.BooleanField("Telegram-уведомления включены", default=False)
    telegram_chat_id = models.CharField("Telegram chat_id", max_length=64, blank=True, default="")

    created_at = models.DateTimeField("Создан", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлён", auto_now=True)

    def __str__(self):
        return f"Profile for {self.user}"

class YandexOAuth(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="yandex_oauth",
    )
    access_token = models.TextField()
    refresh_token = models.TextField(blank=True, null=True)
    expires_at = models.DateTimeField(blank=True, null=True)
    scope = models.CharField(max_length=512, blank=True, default="")
    webmaster_user_id = models.BigIntegerField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def is_expired(self) -> bool:
        return bool(self.expires_at and timezone.now() >= self.expires_at)

    def __str__(self) -> str:
        return f"YandexOAuth(user_id={self.user_id})"

class Issue(models.Model):
    STATUS_OPEN = "open"
    STATUS_RESOLVED = "resolved"
    STATUS_MUTED = "muted"
    STATUS_CHOICES = [
        (STATUS_OPEN, "Open"),
        (STATUS_RESOLVED, "Resolved"),
        (STATUS_MUTED, "Muted"),
    ]

    SEVERITY_FAIL = "fail"
    SEVERITY_WARN = "warn"
    SEVERITY_CHOICES = [
        (SEVERITY_FAIL, "Fail"),
        (SEVERITY_WARN, "Warn"),
    ]

    site = models.ForeignKey("Site", on_delete=models.CASCADE, related_name="issues")
    fingerprint = models.CharField(max_length=128, db_index=True)  # наш dedup-ключ
    check_key = models.CharField(max_length=64, db_index=True)     # http/dns/ssl/traffic/...
    severity = models.CharField(max_length=8, choices=SEVERITY_CHOICES)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_OPEN)

    title = models.CharField(max_length=255, blank=True, default="")
    details = models.JSONField(blank=True, default=dict)

    first_seen_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(auto_now=True)
    last_checkrun = models.ForeignKey(
        "CheckRun",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="issues_seen",
    )

    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ("site", "fingerprint")

    def __str__(self) -> str:
        return f"Issue(site_id={self.site_id}, {self.check_key}, {self.severity}, {self.status})"

    def get_solution_context(self) -> dict:
        """
        Собирает минимальный context для подбора решения.
        Пока опираемся на уже существующие поля title/details без изменения схемы БД.
        """
        context = {}

        details = self.details or {}

        if isinstance(details, dict):
            # HTTP
            if "http_status" in details:
                context["http_status"] = details.get("http_status")

            # Metrics
            if "metric_name" in details:
                context["metric_name"] = details.get("metric_name")

            # На будущее можно будет добавить сюда и другие поля:
            # context["days_left"] = details.get("days_left")
            # context["dns_error_code"] = details.get("dns_error_code")

        return context

    def get_solution(self):
        """
        Возвращает подходящее решение из каталога или None.
        """
        from core.services.solutions import get_solution_for_issue

        return get_solution_for_issue(
            check_key=self.fingerprint.split(":", 1)[0] if self.fingerprint else "",
            severity=self.severity,
            issue_code=self.details.get("issue_code", "") if isinstance(self.details, dict) else "",
            context=self.get_solution_context(),
        )

    @property
    def solution_title(self):
        solution = self.get_solution()
        return solution.title if solution else ""

    @property
    def solution_steps(self):
        solution = self.get_solution()
        return solution.steps if solution else []

    @property
    def solution_short_summary(self):
        solution = self.get_solution()
        return solution.short_summary if solution else ""

class IssueSolution(models.Model):
    SEVERITY_CHOICES = [
        ("warn", "Warning"),
        ("fail", "Fail"),
    ]

    check_key = models.CharField(
        max_length=100,
        help_text="Ключ проверки, например: http, ssl, domain, dns, metrics"
    )
    issue_code = models.CharField(
        max_length=150,
        blank=True,
        default="",
        help_text="Опциональный код/подтип проблемы, например: http_403, ssl_expiring, traffic_drop"
    )
    severity = models.CharField(
        max_length=10,
        choices=SEVERITY_CHOICES,
        help_text="Для какого уровня проблемы подходит решение"
    )

    title = models.CharField(
        max_length=255,
        help_text="Название решения"
    )
    short_summary = models.TextField(
        blank=True,
        default="",
        help_text="Короткое описание, что произошло и что обычно делать"
    )

    steps = models.JSONField(
        default=list,
        blank=True,
        help_text="Список шагов решения, например: ['Проверить X', 'Сделать Y']"
    )
    links = models.JSONField(
        default=list,
        blank=True,
        help_text="Список ссылок, например: [{'label': 'Документация', 'url': 'https://...'}]"
    )

    match_rules = models.JSONField(
        default=dict,
        blank=True,
        help_text=(
            "Доп. условия для матчинга. "
            "Например: {'http_status': 403} или {'metric_name': 'sqi'}"
        )
    )

    priority = models.PositiveIntegerField(
        default=100,
        help_text="Чем меньше число, тем выше приоритет"
    )
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["priority", "id"]
        indexes = [
            models.Index(fields=["check_key", "severity", "is_active"]),
            models.Index(fields=["issue_code", "severity", "is_active"]),
        ]

    def __str__(self):
        code = f" / {self.issue_code}" if self.issue_code else ""
        return f"{self.check_key}{code} [{self.severity}] - {self.title}"