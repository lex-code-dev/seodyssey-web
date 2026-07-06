from django.db import models
from django.conf import settings


class AIQueryResult(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='ai_query_results'
    )
    input_url = models.URLField()
    brand = models.CharField(max_length=255, blank=True)
    theme = models.JSONField(default=list)
    geo = models.JSONField(default=dict)
    ai_queries = models.JSONField(default=list)
    quality = models.JSONField(default=dict)
    elapsed_ms = models.FloatField(null=True, blank=True)
    query_types = models.JSONField(default=dict, blank=True)
    visibility = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.brand} — {self.input_url} ({self.created_at:%d.%m.%Y})"


class WordstatCache(models.Model):
    phrase = models.CharField(max_length=512, unique=True)
    count = models.IntegerField(null=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Wordstat cache'

    def __str__(self):
        return f"{self.phrase}: {self.count}"

class GeoAuditResult(models.Model):
    STATUS_PENDING = 'pending'
    STATUS_RUNNING = 'running'
    STATUS_DONE = 'done'
    STATUS_ERROR = 'error'
    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending'),
        (STATUS_RUNNING, 'Running'),
        (STATUS_DONE, 'Done'),
        (STATUS_ERROR, 'Error'),
    ]
    progress = models.IntegerField(default=0)  # сколько запросов обработано

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='geo_audit_results'
    )
    brand = models.CharField(max_length=255)
    brand_url = models.URLField()
    queries = models.JSONField(default=list)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    result = models.JSONField(null=True, blank=True)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.brand} — {self.status} ({self.created_at:%d.%m.%Y})"