from django.db import models
from .project import ProjectBaseModel


class ProjectRelease(ProjectBaseModel):
    name = models.CharField(max_length=200)
    version = models.CharField(max_length=100)
    notes = models.TextField(blank=True, default="")
    status = models.CharField(max_length=16, default="draft", choices=[("draft", "Draft"), ("published", "Published")])
    published_at = models.DateTimeField(null=True, blank=True)
    app_version = models.CharField(max_length=100, blank=True, default="")
    github_release_id = models.UUIDField(null=True, blank=True)
    pull_request_ids = models.JSONField(default=list, blank=True)
    sources = models.JSONField(default=list, blank=True)

    class Meta:
        db_table = "project_releases"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "version"],
                condition=models.Q(deleted_at__isnull=True),
                name="active_project_release_version_unique",
            )
        ]


class ReleaseIssue(ProjectBaseModel):
    release = models.ForeignKey(ProjectRelease, on_delete=models.CASCADE, related_name="release_items")
    issue = models.ForeignKey("db.Issue", on_delete=models.CASCADE, related_name="release_items")

    class Meta:
        db_table = "release_issues"
        constraints = [
            models.UniqueConstraint(
                fields=["release", "issue"],
                condition=models.Q(deleted_at__isnull=True),
                name="active_release_issue_unique",
            )
        ]
