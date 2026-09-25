import uuid
from django.conf import settings
from django.db import models


class AppConnection(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.OneToOneField("db.Project", on_delete=models.CASCADE, related_name="app_connection")
    origin = models.URLField(max_length=2048)
    current_version = models.CharField(max_length=120, blank=True, default="")
    enabled = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "app_connections"


class PageReview(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey("db.Project", on_delete=models.CASCADE, related_name="page_reviews")
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    status = models.CharField(max_length=20, default="queued")
    request = models.JSONField(default=dict)
    evidence = models.JSONField(default=list)
    results = models.JSONField(default=dict)
    error = models.CharField(max_length=500, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True)

    class Meta:
        db_table = "page_reviews"
        ordering = ["-created_at"]
