from django.db import models
from django.utils import timezone


class AuditResult(models.Model):
    STATUS_PENDING = 'pending'
    STATUS_RUNNING = 'running'
    STATUS_DONE = 'done'
    STATUS_ERROR = 'error'

    STATUS_CHOICES = [
        (STATUS_PENDING, 'Ожидает'),
        (STATUS_RUNNING, 'Выполняется'),
        (STATUS_DONE, 'Завершён'),
        (STATUS_ERROR, 'Ошибка'),
    ]

    site = models.ForeignKey(
        'core.Site',
        on_delete=models.CASCADE,
        related_name='audits'
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    created_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)
    results = models.JSONField(default=dict)
    summary = models.TextField(blank=True)
    block_summaries = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Audit {self.site.domain} — {self.status}"