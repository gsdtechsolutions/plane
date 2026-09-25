from datetime import timedelta
from urllib.parse import urlsplit
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.permissions import BasePermission, SAFE_METHODS
from rest_framework.response import Response
from plane.app.views.base import BaseAPIView
from plane.db.models import AppConnection, PageReview, Project, ProjectMember, WorkspaceMember
from .capture import normalize_origin, public_addresses, selected_url
from .jobs import readable_pages, run_page_review
from .provider import IntelligenceError, provider_config


class ReviewPermission(BasePermission):
    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False
        return (
            ProjectMember.objects.filter(
                project_id=view.project_id,
                workspace__slug=view.workspace_slug,
                member=request.user,
                role__gte=15,
                is_active=True,
            ).exists()
            and WorkspaceMember.objects.filter(
                workspace__slug=view.workspace_slug, member=request.user, is_active=True
            ).exists()
        )


class ConnectionSerializer(serializers.ModelSerializer):
    class Meta:
        model = AppConnection
        fields = ("origin", "current_version", "enabled", "updated_at")
        read_only_fields = ("updated_at",)

    def validate_origin(self, value):
        try:
            origin = normalize_origin(value)
            parsed = urlsplit(origin)
            public_addresses(parsed.hostname, 443 if parsed.scheme == "https" else 80)
            return origin
        except IntelligenceError as exc:
            raise serializers.ValidationError(str(exc))


class ReviewRequestSerializer(serializers.Serializer):
    page_ids = serializers.ListField(child=serializers.UUIDField(), max_length=5, default=list)
    paths = serializers.ListField(child=serializers.CharField(max_length=2048), max_length=3, default=list)
    instructions = serializers.CharField(max_length=2000, allow_blank=True, default="")
    capture_mode = serializers.ChoiceField(choices=("http_only", "browser_rendered"), default="http_only")

    def validate(self, value):
        if not value["page_ids"] and not value["paths"]:
            raise serializers.ValidationError("Select documentation or an app page to review.")
        value["page_ids"] = list(dict.fromkeys(str(id) for id in value["page_ids"]))
        value["paths"] = list(dict.fromkeys(value["paths"]))
        return value


def review_data(job):
    stale = job.status in ("queued", "running") and job.created_at < timezone.now() - timedelta(minutes=5)
    return {
        "id": str(job.id),
        "status": "stalled" if stale else job.status,
        "error": (
            "The worker has not completed this review. Check worker availability and start a new review."
            if stale
            else job.error
        ),
        "request": job.request,
        "evidence": job.evidence,
        "results": job.results,
        "created_at": job.created_at,
        "finished_at": job.finished_at,
    }


class ScopedView(BaseAPIView):
    permission_classes = [ReviewPermission]

    def project(self):
        return get_object_or_404(Project, id=self.project_id, workspace__slug=self.workspace_slug)


class AppConnectionEndpoint(ScopedView):
    def get(self, request, slug, project_id):
        connection = AppConnection.objects.filter(project=self.project()).first()
        return Response(ConnectionSerializer(connection).data if connection else None)

    def put(self, request, slug, project_id):
        project = self.project()
        if (
            not ProjectMember.objects.filter(project=project, member=request.user, role=20, is_active=True).exists()
            and not WorkspaceMember.objects.filter(
                workspace_id=project.workspace_id, member=request.user, role=20, is_active=True
            ).exists()
        ):
            return Response({"error": "Project administrator access is required to configure an app."}, status=403)
        serializer = ConnectionSerializer(AppConnection.objects.filter(project=project).first(), data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(project=project)
        return Response(serializer.data)


class ReviewPagesEndpoint(ScopedView):
    def get(self, request, slug, project_id):
        return Response(list(readable_pages(self.project(), request.user).values("id", "name", "updated_at")[:100]))


class PageReviewEndpoint(ScopedView):
    def get(self, request, slug, project_id, review_id=None):
        # Results may quote private documentation: only their requester can read.
        jobs = PageReview.objects.filter(project=self.project(), requested_by=request.user)
        if review_id:
            return Response(review_data(get_object_or_404(jobs, pk=review_id)))
        return Response([review_data(job) for job in jobs[:20]])

    def post(self, request, slug, project_id, review_id=None):
        if review_id is not None:
            return Response({"error": "Use the collection endpoint to request a review."}, status=405)
        serializer = ReviewRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        project = self.project()
        if readable_pages(project, request.user).filter(pk__in=data["page_ids"]).count() != len(data["page_ids"]):
            return Response({"error": "One or more selected pages are not accessible in this project."}, status=400)
        try:
            provider_config()
            if data["paths"]:
                connection = AppConnection.objects.filter(project=project, enabled=True).first()
                if not connection:
                    raise IntelligenceError("Configure and enable the connected app first.")
                for path in data["paths"]:
                    selected_url(connection.origin, path)
                data["origin"] = connection.origin
        except IntelligenceError as exc:
            return Response({"error": str(exc)}, status=400)
        # Per-project row lock makes the queue limit race-safe.
        with transaction.atomic():
            Project.objects.select_for_update().get(pk=project.pk)
            if (
                PageReview.objects.filter(
                    project=project, created_at__gte=timezone.now() - timedelta(minutes=5)
                ).count()
                >= 5
            ):
                return Response(
                    {"error": "Five reviews were recently requested. Try again in a few minutes."}, status=429
                )
            job = PageReview.objects.create(project=project, requested_by=request.user, request=data)
        try:
            run_page_review.apply_async(args=[str(job.id)], queue="release_intelligence", retry=False)
        except Exception:
            job.status = "failed"
            job.error = "The review worker is unavailable. Contact your instance administrator."
            job.finished_at = timezone.now()
            job.save(update_fields=["status", "error", "finished_at"])
            return Response(review_data(job), status=503)
        return Response(review_data(job), status=202)


class ReviewCapabilitiesEndpoint(ScopedView):
    def get(self, request, slug, project_id):
        import importlib.util
        import os

        self.project()
        try:
            provider_config()
            ai_error = ""
        except IntelligenceError as exc:
            ai_error = str(exc)
        browser_available = (
            os.environ.get("RELEASE_BROWSER_ENABLED") == "1" and importlib.util.find_spec("playwright") is not None
        )
        return Response(
            {
                "ai_configured": not ai_error,
                "ai_error": ai_error,
                "browser_available": browser_available,
                "browser_message": (
                    "Browser worker configured; Chromium startup is checked when a job runs."
                    if browser_available
                    else "Browser review needs the optional Playwright/Chromium worker and RELEASE_BROWSER_ENABLED=1. HTTP evidence is available now."
                ),
            }
        )
