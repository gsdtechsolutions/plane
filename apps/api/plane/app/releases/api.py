from django.db import transaction, IntegrityError
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.html import strip_tags
from rest_framework.permissions import BasePermission, AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from plane.app.views.base import BaseAPIView
from plane.db.models import Project, ProjectMember, WorkspaceMember, ProjectRelease, DeployBoard, Issue
from .serializers import ReleaseSerializer


class ReleasePermission(BasePermission):
    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False
        slug, project_id = view.kwargs["slug"], view.kwargs["project_id"]
        if not WorkspaceMember.objects.filter(workspace__slug=slug, member=request.user, is_active=True).exists():
            return False
        role = (
            ProjectMember.objects.filter(
                project_id=project_id, workspace__slug=slug, member=request.user, is_active=True
            )
            .values_list("role", flat=True)
            .first()
        )
        minimum = 15
        if view.kwargs.get("action") in ("publish", "unpublish"):
            minimum = 20
        return role is not None and role >= minimum


class ReleaseEndpoint(BaseAPIView):
    permission_classes = [ReleasePermission]

    def project(self):
        return get_object_or_404(Project, pk=self.kwargs["project_id"], workspace__slug=self.kwargs["slug"])

    def release(self, lock=False):
        qs = ProjectRelease.objects.select_for_update() if lock else ProjectRelease.objects
        return get_object_or_404(qs, pk=self.kwargs["release_id"], project=self.project())

    def serialized(self, release):
        return ReleaseSerializer(release, context={"project": self.project()}).data

    def get(self, request, slug, project_id, release_id=None):
        project = self.project()
        if release_id:
            return Response(self.serialized(self.release()))
        board = DeployBoard.objects.filter(
            workspace=project.workspace, entity_name="project", entity_identifier=project.id, is_disabled=False
        ).first()
        return Response(
            {
                "releases": ReleaseSerializer(
                    ProjectRelease.objects.filter(project=project), many=True, context={"project": project}
                ).data,
                "public_anchor": board.anchor if board else None,
            }
        )

    def post(self, request, slug, project_id, release_id=None, action=None):
        if action in ("publish", "unpublish"):
            with transaction.atomic():
                release = self.release(lock=True)
                if action == "publish":
                    try:
                        expected = parse_datetime(request.data.get("expected_updated_at", ""))
                    except (ValueError, TypeError):
                        expected = None
                    if expected != release.updated_at:
                        return Response(
                            {"error": "This draft changed. Reload and review its latest notes before publishing."},
                            status=409,
                        )
                    if not release.notes.strip():
                        return Response({"error": "Add release notes before publishing."}, status=400)
                    release.status = "published"
                    release.published_at = release.published_at or timezone.now()
                else:
                    release.status = "draft"
                    release.published_at = None
                release.save()
                return Response(self.serialized(release))
        if action == "generate":
            return self.generate(request)
        if release_id:
            return Response({"error": "Use PATCH to edit a release."}, status=405)
        serializer = ReleaseSerializer(data=request.data, context={"project": self.project()})
        serializer.is_valid(raise_exception=True)
        try:
            serializer.save()
        except IntegrityError:
            return Response({"error": "This release version already exists."}, status=400)
        return Response(serializer.data, status=201)

    def patch(self, request, slug, project_id, release_id):
        with transaction.atomic():
            serializer = ReleaseSerializer(
                self.release(lock=True), data=request.data, partial=True, context={"project": self.project()}
            )
            serializer.is_valid(raise_exception=True)
            try:
                serializer.save()
            except IntegrityError:
                return Response({"error": "This release version already exists."}, status=400)
            return Response(serializer.data)

    def delete(self, request, slug, project_id, release_id):
        with transaction.atomic():
            release = self.release(lock=True)
            if release.status == "published":
                return Response({"error": "Unpublish before deleting this release."}, status=400)
            release.delete()
        return Response(status=204)

    def generate(self, request):
        release = self.release()
        if release.status != "draft":
            return Response({"error": "Unpublish before generating a new draft."}, status=400)
        stamp = release.updated_at
        sources = []
        for issue in Issue.objects.filter(
            release_items__release=release, release_items__deleted_at__isnull=True
        ).select_related("project", "workspace"):
            sources.append(
                {
                    "type": "work_item",
                    "id": str(issue.id),
                    "title": issue.name,
                    "text": f"{issue.name}\n{strip_tags(issue.description_html or chr(32))}"[:5000],
                    "url": f"/{issue.workspace.slug}/browse/{issue.project.identifier}-{issue.sequence_id}/",
                }
            )
        if release.pull_request_ids or release.github_release_id:
            from plane.app.github_delivery.services import get_release_sources

            sources.extend(get_release_sources(release.project, release.pull_request_ids, release.github_release_id))
        if not sources:
            return Response({"error": "Link work items or pull requests before generating notes."}, status=400)
        instructions = request.data.get("instructions", "")
        if not isinstance(instructions, str) or len(instructions) > 2000:
            return Response({"error": "Instructions must be text under 2000 characters."}, status=400)
        from plane.app.release_intelligence.services import generate_release_summary, IntelligenceError

        try:
            result = generate_release_summary(release.project, sources, instructions)
        except IntelligenceError as exc:
            return Response({"error": str(exc)}, status=503)
        with transaction.atomic():
            current = self.release(lock=True)
            if current.status != "draft" or current.updated_at != stamp:
                return Response(
                    {"error": "This draft changed during generation. Retry to use its latest content."}, status=409
                )
            current.notes = result["text"][:50000]
            current.sources = result["sources"]
            current.save()
        return Response(self.serialized(current))


class ReleaseActionEndpoint(ReleaseEndpoint):
    http_method_names = ["post", "options"]


class ReleaseOptionsEndpoint(BaseAPIView):
    permission_classes = [ReleasePermission]

    def get(self, request, slug, project_id):
        project = get_object_or_404(Project, pk=project_id, workspace__slug=slug)
        search = request.query_params.get("search", "")[:100]
        issues = Issue.objects.filter(project=project, name__icontains=search).order_by("-created_at")[:100]
        return Response(
            {
                "issues": [
                    {"id": str(i.id), "name": i.name, "identifier": f"{project.identifier}-{i.sequence_id}"}
                    for i in issues
                ]
            }
        )


class PublicReleasesEndpoint(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request, anchor):
        board = get_object_or_404(DeployBoard, anchor=anchor, entity_name="project", is_disabled=False)
        project = get_object_or_404(Project, pk=board.entity_identifier, workspace=board.workspace)
        releases = ProjectRelease.objects.filter(
            project=project, status="published", published_at__isnull=False
        ).order_by("-published_at")[:50]
        response = Response(
            {
                "project_name": project.name,
                "releases": [
                    {
                        "id": str(r.id),
                        "name": r.name,
                        "version": r.version,
                        "notes": r.notes,
                        "published_at": r.published_at.isoformat(),
                        "app_version": r.app_version or None,
                    }
                    for r in releases
                ],
            }
        )
        response["Access-Control-Allow-Origin"] = "*"
        response["Cache-Control"] = "no-store"
        return response


class PublicIssueReleasesEndpoint(PublicReleasesEndpoint):
    def get(self, request, anchor, issue_id):
        from plane.space.utils.visibility import published_board, public_issues

        board = published_board(anchor)
        issue = get_object_or_404(public_issues(board), id=issue_id)
        releases = ProjectRelease.objects.filter(
            project_id=issue.project_id,
            status="published",
            published_at__isnull=False,
            release_items__issue=issue,
            release_items__deleted_at__isnull=True,
        ).order_by("-published_at")[:50]
        return Response(
            {
                "releases": [
                    {
                        "id": str(r.id),
                        "name": r.name,
                        "version": r.version,
                        "notes": r.notes,
                        "published_at": r.published_at.isoformat(),
                        "app_version": r.app_version or None,
                    }
                    for r in releases
                ]
            }
        )
