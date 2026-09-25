# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from django.db import transaction
from django.db.models import Sum
from django.shortcuts import get_object_or_404
from django.utils import timezone

from rest_framework import status
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission, SAFE_METHODS
from rest_framework.response import Response

from plane.app.timetracking.serializers import TimeEntrySerializer
from plane.app.views.base import BaseAPIView, BaseViewSet
from plane.db.models import Issue, Project, ProjectMember, TimeEntry, User, WorkspaceMember


def get_time_tracking_project(*, slug, project_id):
    """Resolve the URL project and enforce the feature gate.

    Raises 404 when the project does not exist in the workspace and 403 with
    a clear message when time tracking is disabled for it.
    """
    project = get_object_or_404(Project, pk=project_id, workspace__slug=slug)
    if not project.is_time_tracking_enabled:
        raise PermissionDenied(
            "Time tracking is not enabled for this project. Enable it in the project settings first."
        )
    return project


def running_timer_queryset(user):
    """Open timers for a user: started but never ended."""
    return TimeEntry.objects.filter(user=user, started_at__isnull=False, ended_at__isnull=True)


def minutes_between(started_at, ended_at):
    """Whole minutes between two datetimes, clamped at zero (clock skew guard)."""
    if started_at is None or ended_at is None or ended_at <= started_at:
        return 0
    return max(0, int(round((ended_at - started_at).total_seconds() / 60)))


class TimeTrackingPermission(BasePermission):
    """Any active project member may read and create time entries.

    Updates and deletes are additionally restricted to the entry creator or
    a project/workspace admin (has_object_permission).
    """

    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False
        if not WorkspaceMember.objects.filter(
            workspace__slug=view.workspace_slug, member=request.user, is_active=True
        ).exists():
            return False
        return ProjectMember.objects.filter(
            workspace__slug=view.workspace_slug,
            project_id=view.project_id,
            member=request.user,
            is_active=True,
        ).exists()

    def has_object_permission(self, request, view, obj):
        if request.method in SAFE_METHODS:
            return True
        if obj.user_id == request.user.id:
            return True
        return (
            ProjectMember.objects.filter(
                project_id=view.project_id, member=request.user, is_active=True, role__gte=20
            ).exists()
            or WorkspaceMember.objects.filter(
                workspace__slug=view.workspace_slug, member=request.user, is_active=True, role__gte=20
            ).exists()
        )


class TimeEntryViewSet(BaseViewSet):
    """Issue-scoped CRUD for time entries.

    Every query is bound to the workspace slug + project id + issue id from
    the URL, so an entry from another issue/project/workspace is never
    reachable (404, not 403).
    """

    serializer_class = TimeEntrySerializer
    model = TimeEntry
    permission_classes = [TimeTrackingPermission]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        # Feature gate for every action on this view.
        self.project = get_time_tracking_project(slug=self.workspace_slug, project_id=self.project_id)

    def get_queryset(self):
        return (
            TimeEntry.objects.filter(
                workspace__slug=self.workspace_slug,
                project_id=self.project_id,
                issue_id=self.kwargs.get("issue_id"),
                deleted_at__isnull=True,
            )
            .select_related("user", "issue", "project")
            .order_by("-created_at")
        )

    def get_object(self):
        queryset = self.get_queryset()
        obj = get_object_or_404(queryset, id=self.kwargs.get("entry_id"))
        # May raise a permission denied (no-op for class-level permissions).
        self.check_object_permissions(self.request, obj)
        return obj

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["project"] = self.project
        return context

    def perform_create(self, serializer):
        issue = get_object_or_404(Issue, pk=self.kwargs.get("issue_id"), project=self.project)
        serializer.save(project=self.project, issue=issue, user=self.request.user)

    def perform_update(self, serializer):
        serializer.save()

    def perform_destroy(self, instance):
        instance.delete()


class IssueTimeEntrySummaryEndpoint(BaseAPIView):
    """GET: rollup for one issue — total minutes, per-user breakdown, count."""

    permission_classes = [TimeTrackingPermission]

    def get(self, request, slug, project_id, issue_id):
        project = get_time_tracking_project(slug=slug, project_id=project_id)
        get_object_or_404(Issue, pk=issue_id, project=project)

        entries = TimeEntry.objects.filter(
            workspace__slug=slug,
            project=project,
            issue_id=issue_id,
            deleted_at__isnull=True,
        )
        total_minutes = entries.aggregate(total=Sum("minutes"))["total"] or 0
        entries_count = entries.count()

        user_rows = list(
            entries.values("user_id").annotate(minutes=Sum("minutes")).order_by("-minutes")
        )
        users = {
            user.id: user.display_name or user.email
            for user in User.objects.filter(id__in=[row["user_id"] for row in user_rows])
        }
        by_user = [
            {
                "user_id": str(row["user_id"]),
                "display_name": users.get(row["user_id"], ""),
                "minutes": row["minutes"],
            }
            for row in user_rows
        ]

        return Response(
            {"total_minutes": total_minutes, "entries_count": entries_count, "by_user": by_user},
            status=status.HTTP_200_OK,
        )


class TimerStartEndpoint(BaseAPIView):
    """POST: start the caller's timer on this issue.

    Stop-then-start semantics: any other open timer of the user (on any
    issue, project, or workspace) is closed first with minutes computed
    from its started_at, then a fresh open entry is created here. This
    enforces at most one running timer per user globally.
    """

    permission_classes = [TimeTrackingPermission]

    @transaction.atomic
    def post(self, request, slug, project_id, issue_id):
        project = get_time_tracking_project(slug=slug, project_id=project_id)
        issue = get_object_or_404(Issue, pk=issue_id, project=project)

        now = timezone.now()
        for entry in running_timer_queryset(request.user).select_for_update():
            entry.ended_at = now
            entry.minutes = minutes_between(entry.started_at, now)
            entry.save(update_fields=["ended_at", "minutes", "updated_at"])

        entry = TimeEntry.objects.create(
            project=project,
            issue=issue,
            user=request.user,
            started_at=now,
            minutes=0,
        )
        return Response(TimeEntrySerializer(entry).data, status=status.HTTP_201_CREATED)


class TimerStopEndpoint(BaseAPIView):
    """POST: stop the caller's running timer.

    Without a body, the user's open timer (globally unique) is closed.
    A body {"issue_id": ...} targets an open timer on that issue instead,
    which supports stopping a timer that is running on a different issue
    than the one in the URL. minutes is set to the rounded diff from
    started_at; the entry is left untouched when there is nothing running.
    """

    permission_classes = [TimeTrackingPermission]

    @transaction.atomic
    def post(self, request, slug, project_id, issue_id):
        project = get_time_tracking_project(slug=slug, project_id=project_id)

        open_entries = running_timer_queryset(request.user).select_for_update()
        body_issue_id = request.data.get("issue_id") if isinstance(request.data, dict) else None
        entry = None
        if body_issue_id:
            entry = open_entries.filter(issue_id=body_issue_id).first()
        if entry is None:
            entry = open_entries.first()
        if entry is None:
            return Response({"error": "No running timer for this user."}, status=status.HTTP_400_BAD_REQUEST)

        now = timezone.now()
        entry.ended_at = now
        entry.minutes = minutes_between(entry.started_at, now)
        entry.save(update_fields=["ended_at", "minutes", "updated_at"])
        return Response(TimeEntrySerializer(entry).data, status=status.HTTP_200_OK)


class WorkspaceTimeEntryEndpoint(BaseAPIView):
    """GET: workspace-wide paginated time entries for the requester.

    Optional query filters: project_id, issue_id, start_date, end_date
    (both inclusive, applied to created_at). Entries from projects with
    time tracking disabled are never returned, and the queryset is always
    limited to projects the requester is an active member of.
    """

    def get(self, request, slug):
        if not WorkspaceMember.objects.filter(
            workspace__slug=slug, member=request.user, is_active=True
        ).exists():
            return Response({"error": "You are not a member of this workspace."}, status=status.HTTP_403_FORBIDDEN)

        entries = TimeEntry.objects.filter(
            workspace__slug=slug,
            deleted_at__isnull=True,
            project__is_time_tracking_enabled=True,
            project__project_projectmember__member=request.user,
            project__project_projectmember__is_active=True,
            project__archived_at__isnull=True,
        )

        project_id = request.query_params.get("project_id")
        if project_id:
            entries = entries.filter(project_id=project_id)
        issue_id = request.query_params.get("issue_id")
        if issue_id:
            entries = entries.filter(issue_id=issue_id)
        start_date = request.query_params.get("start_date")
        if start_date:
            entries = entries.filter(created_at__date__gte=start_date)
        end_date = request.query_params.get("end_date")
        if end_date:
            entries = entries.filter(created_at__date__lte=end_date)

        entries = entries.select_related("user", "issue", "project").order_by("-created_at")

        return self.paginate(
            request=request,
            queryset=entries,
            on_results=lambda rows: TimeEntrySerializer(rows, many=True).data,
            default_per_page=50,
        )
