# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import hmac
import os
from html import escape

from django.conf import settings
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from plane.app.ai_ops.service import log_ai_action
from plane.app.views.base import BaseAPIView
from plane.db.models import Issue, IssueComment, Project, ProjectMember, Workspace, WorkspaceMember
from plane.db.models.agent_delegation import DelegationRun
from plane.db.models.ai_audit import AIActionAudit
from plane.license.utils.instance_value import get_configuration_value


def workspace_admin(user, slug):
    workspace = get_object_or_404(Workspace, slug=slug)
    if (
        not user.is_authenticated
        or not WorkspaceMember.objects.filter(workspace=workspace, member=user, role=20, is_active=True).exists()
    ):
        raise PermissionDenied("Workspace administrators manage agent delegations.")
    return workspace


def project_member(user, slug, project_id, *, admin=False):
    project = get_object_or_404(Project, id=project_id, workspace__slug=slug)
    if (
        not user.is_authenticated
        or not WorkspaceMember.objects.filter(workspace=project.workspace, member=user, is_active=True).exists()
    ):
        raise PermissionDenied()
    members = ProjectMember.objects.filter(project=project, member=user, is_active=True, role__gte=15)
    if not members.exists() or admin and not members.filter(role=20).exists():
        raise PermissionDenied("An active project membership is required.")
    return project


def configuration(key, default=""):
    fallback = getattr(settings, key, None) or os.environ.get(key, default)
    (value,) = get_configuration_value([{"key": key, "default": fallback}])
    return value or default


def runner_key_ok(request):
    expected = configuration("AGENT_RUNNER_KEY")
    supplied = request.headers.get("X-Runner-Key", "")
    if not expected or not hmac.compare_digest(supplied.encode(), str(expected).encode()):
        raise PermissionDenied("Runner access denied.")
    return True


def issue_display(issue):
    return f"{issue.project.identifier}-{issue.sequence_id}"


class DelegationRunSerializer(serializers.ModelSerializer):
    issue = serializers.SerializerMethodField()
    created_by = serializers.SerializerMethodField()

    class Meta:
        model = DelegationRun
        fields = (
            "id",
            "status",
            "instructions",
            "branch",
            "pr_url",
            "pr_number",
            "result_excerpt",
            "error",
            "runner_id",
            "created_at",
            "claimed_at",
            "started_at",
            "finished_at",
            "issue",
            "created_by",
        )

    def get_issue(self, obj):
        return {"id": str(obj.issue_id), "display": issue_display(obj.issue)}

    def get_created_by(self, obj):
        return {"id": str(obj.created_by_id), "email": obj.created_by.email} if obj.created_by_id else None


class TextField(serializers.CharField):
    def to_internal_value(self, data):
        if not isinstance(data, str):
            self.fail("invalid")
        return super().to_internal_value(data)


class CreateSerializer(serializers.Serializer):
    instructions = TextField(required=False, default="", allow_blank=True, max_length=8000, trim_whitespace=False)


class ClaimSerializer(serializers.Serializer):
    runner_id = TextField(max_length=128)


class EventSerializer(ClaimSerializer):
    status = serializers.ChoiceField(choices=("running", "pr_opened", "completed", "failed", "cancelled"))
    branch = TextField(required=False, allow_blank=True, max_length=255)
    pr_url = serializers.URLField(required=False, allow_blank=True, max_length=200)
    pr_number = serializers.IntegerField(required=False, allow_null=True, min_value=1, max_value=2147483647)
    result_excerpt = TextField(required=False, allow_blank=True, max_length=8000, trim_whitespace=False)
    error = TextField(required=False, allow_blank=True, max_length=8000, trim_whitespace=False)


def comment_and_audit(run, message, action, *, failed=False):
    IssueComment.objects.create(
        workspace=run.workspace,
        project=run.project,
        issue=run.issue,
        actor=run.created_by,
        created_by=run.created_by,
        comment_html=f"<p>{escape(message)}</p>",
    )
    # A savepoint keeps best-effort auditing from breaking the state transition.
    with transaction.atomic():
        log_ai_action(
            workspace=run.workspace,
            project=run.project,
            actor=run.created_by,
            action=action,
            entity_type="issue",
            entity_id=run.issue_id,
            status=AIActionAudit.Status.ERROR if failed else AIActionAudit.Status.SUCCESS,
            input_excerpt=run.instructions,
            output_excerpt=run.result_excerpt,
            error=run.error,
            metadata={"run_id": str(run.id), "pr_url": run.pr_url},
        )


def scoped_issue(request, slug, project_id, issue_id):
    project = project_member(request.user, slug, project_id)
    return get_object_or_404(Issue, id=issue_id, project=project, workspace=project.workspace)


def runs():
    return DelegationRun.objects.select_related("workspace", "project", "issue__project", "created_by")


class DelegationListEndpoint(BaseAPIView):
    @transaction.atomic
    def post(self, request, slug, project_id, issue_id):
        issue = scoped_issue(request, slug, project_id, issue_id)
        serializer = CreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        run = DelegationRun.objects.create(
            workspace=issue.workspace,
            project=issue.project,
            issue=issue,
            created_by=request.user,
            **serializer.validated_data,
        )
        comment_and_audit(run, f"🤖 Delegated to coding agent — run {str(run.id)[:8]} queued.", "agent.delegate")
        return Response(DelegationRunSerializer(run).data, status=201)

    def get(self, request, slug, project_id, issue_id):
        issue = scoped_issue(request, slug, project_id, issue_id)
        queryset = runs().filter(issue=issue, project=issue.project, workspace=issue.workspace)
        try:
            offset = max(int(request.query_params.get("offset", 0)), 0)
        except (TypeError, ValueError):
            offset = 0
        try:
            limit = min(max(int(request.query_params.get("limit", 50)), 1), 200)
        except (TypeError, ValueError):
            limit = 50
        return Response(
            {
                "count": queryset.count(),
                "offset": offset,
                "limit": limit,
                "results": DelegationRunSerializer(queryset[offset : offset + limit], many=True).data,
            }
        )


class DelegationDetailEndpoint(BaseAPIView):
    def get(self, request, slug, project_id, issue_id, run_id):
        issue = scoped_issue(request, slug, project_id, issue_id)
        run = get_object_or_404(runs(), id=run_id, issue=issue, project=issue.project, workspace=issue.workspace)
        return Response(DelegationRunSerializer(run).data)


class RunnerEndpoint(BaseAPIView):
    authentication_classes = []
    permission_classes = [AllowAny]


class RunnerHealthEndpoint(RunnerEndpoint):
    def get(self, request):
        runner_key_ok(request)
        return Response({"ok": True})


class RunnerClaimEndpoint(RunnerEndpoint):
    @transaction.atomic
    def post(self, request):
        runner_key_ok(request)
        serializer = ClaimSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        run = (
            runs()
            .select_related("issue__state")
            .select_for_update(of=("self",), skip_locked=True)
            .filter(status=DelegationRun.Status.QUEUED)
            .order_by("created_at", "id")
            .first()
        )
        if run is None:
            return Response(status=204)
        display = issue_display(run.issue)
        run.status = DelegationRun.Status.CLAIMED
        run.runner_id = serializer.validated_data["runner_id"]
        run.claimed_at = timezone.now()
        run.branch = run.branch or f"agent/{display.lower()}-{str(run.id)[:8]}"
        run.save(update_fields=["status", "runner_id", "claimed_at", "branch", "updated_at"])
        return Response(
            {
                "id": str(run.id),
                "status": run.status,
                "issue": {
                    "workspace_slug": run.workspace.slug,
                    "workspace_id": str(run.workspace_id),
                    "project_id": str(run.project_id),
                    "project_identifier": run.project.identifier,
                    "issue_id": str(run.issue_id),
                    "issue_url": (
                        f"{getattr(settings, 'WEB_URL', '').rstrip('/')}/{run.workspace.slug}/"
                        f"projects/{run.project_id}/issues/{run.issue_id}"
                    )
                    if getattr(settings, "WEB_URL", "")
                    else "",
                    "issue_name": run.issue.name,
                    "issue_display": display,
                    "description_html": run.issue.description_html,
                    "state_name": run.issue.state.name if run.issue.state_id else "",
                    "assignee_emails": list(run.issue.assignees.values_list("email", flat=True)),
                    "instructions": run.instructions,
                    "branch": run.branch,
                    "repo": configuration("AGENT_REPO_URL", "gsdtechsolutions/plane"),
                },
            }
        )


TRANSITIONS = {
    "claimed": {"running", "failed", "cancelled"},
    "running": {"pr_opened", "completed", "failed", "cancelled"},
    "pr_opened": {"completed", "failed", "cancelled"},
}


class RunnerEventEndpoint(RunnerEndpoint):
    @transaction.atomic
    def post(self, request, run_id):
        runner_key_ok(request)
        serializer = EventSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = dict(serializer.validated_data)
        run = get_object_or_404(runs().select_for_update(of=("self",)), id=run_id)
        if not run.runner_id or run.runner_id != values.pop("runner_id"):
            raise PermissionDenied("Runner access denied.")
        status = values.pop("status")
        # Retried acknowledgements never duplicate comments or audit rows.
        if status == run.status:
            return Response(DelegationRunSerializer(run).data)
        if status not in TRANSITIONS.get(run.status, set()):
            return Response({"error": "Illegal delegation status transition."}, status=409)
        if run.status == "running" and status == "completed" and (values.get("pr_url") or run.pr_url):
            return Response({"error": "Record pr_opened before completing a PR run."}, status=409)
        if status == "pr_opened" and not (values.get("pr_url") or run.pr_url):
            return Response({"error": "A PR URL is required."}, status=400)
        for field, value in values.items():
            setattr(run, field, value)
        run.status = status
        if status == "running":
            run.started_at = timezone.now()
        if status in {"completed", "failed", "cancelled"}:
            run.finished_at = timezone.now()
        run.save()
        if status == "pr_opened":
            comment_and_audit(run, f"🤖 Coding agent opened draft PR: {run.pr_url}", "agent.pr_opened")
        elif status == "completed":
            message = (
                f"🤖 Coding agent finished — PR ready for review: {run.pr_url}"
                if run.pr_url
                else "🤖 Coding agent finished — agent made no changes."
            )
            comment_and_audit(run, message, "agent.completed")
        elif status == "failed":
            comment_and_audit(run, f"🤖 Coding agent failed: {run.error[:500]}", "agent.failed", failed=True)
        return Response(DelegationRunSerializer(run).data)
