from django.db import models

class Site(models.Model):
    name = models.CharField(max_length=200)
    url = models.URLField()
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} ({self.url})"


class CheckRun(models.Model):
    site = models.ForeignKey(Site, on_delete=models.CASCADE, related_name="checks")
    created_at = models.DateTimeField(auto_now_add=True)

    is_up = models.BooleanField(default=False)
    http_status = models.IntegerField(null=True, blank=True)
    error = models.TextField(blank=True, default="")

    def __str__(self):
        return f"{self.site.name} @ {self.created_at:%Y-%m-%d %H:%M} up={self.is_up}"
