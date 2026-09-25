from uuid import UUID
from django.db import transaction
from rest_framework import serializers
from plane.db.models import Issue, ProjectRelease, ReleaseIssue


class ReleaseSerializer(serializers.ModelSerializer):
    issue_ids = serializers.ListField(child=serializers.UUIDField(), max_length=200, required=False, write_only=True)
    issues = serializers.SerializerMethodField()
    pull_request_ids = serializers.ListField(child=serializers.UUIDField(), max_length=200, required=False)
    notes = serializers.CharField(max_length=50000, allow_blank=True, required=False)

    class Meta:
        model = ProjectRelease
        fields = [
            "id",
            "name",
            "version",
            "notes",
            "status",
            "published_at",
            "app_version",
            "github_release_id",
            "pull_request_ids",
            "sources",
            "issue_ids",
            "issues",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "status", "published_at", "sources", "created_at", "updated_at"]
        validators = []

    def get_issues(self, obj):
        return [
            {"id": str(i.id), "name": i.name, "identifier": f"{i.project.identifier}-{i.sequence_id}"}
            for i in Issue.objects.filter(
                release_items__release=obj, release_items__deleted_at__isnull=True
            ).select_related("project")
        ]

    def validate(self, attrs):
        project = self.context["project"]
        if self.instance and self.instance.status == "published":
            raise serializers.ValidationError("Unpublish this release before editing its draft.")
        version = attrs.get("version", getattr(self.instance, "version", "")).strip()
        if not version:
            raise serializers.ValidationError({"version": "A version is required."})
        duplicate = ProjectRelease.objects.filter(project=project, version=version)
        if self.instance:
            duplicate = duplicate.exclude(pk=self.instance.pk)
        if duplicate.exists():
            raise serializers.ValidationError({"version": "This project already has this version."})
        attrs["version"] = version
        if "issue_ids" in attrs:
            ids = set(attrs["issue_ids"])
            if Issue.objects.filter(project=project, pk__in=ids).count() != len(ids):
                raise serializers.ValidationError({"issue_ids": "Select work items belonging to this project."})
        if attrs.get("github_release_id") or attrs.get("pull_request_ids"):
            # The GitHub lane provides project-scoped adapter validation.
            from plane.app.github_delivery.services import list_project_releases

            releases = {UUID(str(r["id"])) for r in list_project_releases(project)}
            if attrs.get("github_release_id") and attrs["github_release_id"] not in releases:
                raise serializers.ValidationError({"github_release_id": "Choose a release from a mapped repository."})
            ids = {str(i) for i in attrs.get("pull_request_ids", [])}
            if ids:
                from plane.app.github_delivery.services import validate_project_pull_requests

                if not validate_project_pull_requests(project, ids):
                    raise serializers.ValidationError(
                        {"pull_request_ids": "Choose pull requests from a mapped repository."}
                    )
        if "pull_request_ids" in attrs:
            attrs["pull_request_ids"] = [str(i) for i in dict.fromkeys(attrs["pull_request_ids"])]
        return attrs

    def _items(self, release, ids):
        if ids is not None:
            ReleaseIssue.objects.filter(release=release).delete()
            for issue in Issue.objects.filter(project=release.project, pk__in=set(ids)):
                ReleaseIssue.objects.create(project=release.project, release=release, issue=issue)

    @transaction.atomic
    def create(self, validated_data):
        ids = validated_data.pop("issue_ids", [])
        release = ProjectRelease.objects.create(project=self.context["project"], **validated_data)
        self._items(release, ids)
        return release

    @transaction.atomic
    def update(self, instance, validated_data):
        ids = validated_data.pop("issue_ids", None)
        instance = super().update(instance, validated_data)
        self._items(instance, ids)
        return instance
