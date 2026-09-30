# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Reviewable AI triage suggestions on the issue detail.

GET lists pending suggestions first, then decided ones. POST accept applies a
pending suggestion per kind (labels and assignees are written as DIRECT
through-rows — .set() trips IntegrityError on this fork — and priority is a
validated single-field update); POST dismiss just marks the row. Everything is
audited via log_ai_action; project membership gates every endpoint."""

from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from plane.app.ai_ops.service import log_ai_action
from plane.app.views.base import BaseAPIView
from plane.db.models import (
    Issue,
    IssueAssignee,
    IssueLabel,
    Label,
    Project,
    ProjectMember,
    Workspace,
    WorkspaceMember,
)
from plane.db.models.ai_triage import AIIssueSuggestion


def workspace_admin(user, slug):
    workspace = get_object_or_404(Workspace, slug=slug)
    if (
        not user.is_authenticated
        or not WorkspaceMember.objects.filter(workspace=workspace, member=user, role=20, is_active=True).exists()
    ):
        raise PermissionDenied("Workspace administrators manage workspace settings.")
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


class AIIssueSuggestionSerializer(serializers.ModelSerializer):
    decided_by_email = serializers.SerializerMethodField()

    class Meta:
        model = AIIssueSuggestion
        fields = (
            "id",
            "kind",
            "payload",
            "confidence",
            "status",
            "model",
            "created_at",
            "decided_at",
            "decided_by",
            "decided_by_email",
        )

    def get_decided_by_email(self, obj):
        if obj.decided_by_id is None:
            return None
        return getattr(obj.decided_by, "email", None)


def _get_issue(issue_id, project):
    return get_object_or_404(Issue, id=issue_id, project=project, workspace=project.workspace)


def _get_suggestion(sid, issue_id, project):
    return get_object_or_404(AIIssueSuggestion, id=sid, issue_id=issue_id, project=project)


def _decide(suggestion, request, new_status):
    suggestion.status = new_status
    suggestion.decided_by = request.user
    suggestion.decided_at = timezone.now()
    suggestion.save(update_fields=["status", "decided_by", "decided_at"])
    return suggestion


class TriageSuggestionsEndpoint(BaseAPIView):
    """List the AI triage suggestions for an issue: pending first, newest first."""

    def get(self, request, slug, project_id, issue_id):
        project = project_member(request.user, slug, project_id)
        issue = _get_issue(issue_id, project)
        suggestions = list(
            AIIssueSuggestion.objects.filter(issue=issue).order_by("-created_at")[:200]
        )
        suggestions.sort(key=lambda row: 0 if row.status == AIIssueSuggestion.Status.PENDING else 1)
        return Response(
            {
                "count": len(suggestions),
                "results": AIIssueSuggestionSerializer(suggestions, many=True).data,
            },
            status=status.HTTP_200_OK,
        )


class TriageSuggestionAcceptEndpoint(BaseAPIView):
    """Apply a pending suggestion to the issue, per kind. Reviewable, never silent."""

    def post(self, request, slug, project_id, issue_id, sid):
        project = project_member(request.user, slug, project_id)
        issue = _get_issue(issue_id, project)
        suggestion = _get_suggestion(sid, issue.pk, project)

        if suggestion.status != AIIssueSuggestion.Status.PENDING:
            return Response(
                {"error": "This suggestion has already been decided."},
                status=status.HTTP_409_CONFLICT,
            )

        payload = suggestion.payload or {}
        if suggestion.kind == AIIssueSuggestion.Kind.LABEL:
            name = str(payload.get("name", "")).strip()
            if not name:
                return Response({"error": "Suggestion payload is missing a label name."}, status=status.HTTP_400_BAD_REQUEST)
            # Case-insensitive against existing project labels; create only when
            # every existing label vanished since the suggestion was made.
            label = Label.objects.filter(project=project, name__iexact=name, deleted_at__isnull=True).first()
            if label is None:
                label = Label.objects.create(project=project, name=name)
            through, _ = IssueLabel.objects.get_or_create(
                issue=issue,
                label=label,
                defaults={"project": project, "workspace": project.workspace},
            )
            applied = {"label_id": str(label.id), "label_name": label.name, "issue_label_id": str(through.id)}
        elif suggestion.kind == AIIssueSuggestion.Kind.ASSIGNEE:
            user_id = payload.get("user_id")
            if not user_id or not ProjectMember.objects.filter(
                project=project, member_id=user_id, is_active=True
            ).exists():
                return Response(
                    {"error": "Suggested assignee is no longer a project member."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            through, _ = IssueAssignee.objects.get_or_create(
                issue=issue,
                assignee_id=user_id,
                defaults={"project": project, "workspace": project.workspace},
            )
            applied = {"assignee_id": str(user_id), "issue_assignee_id": str(through.id)}
        elif suggestion.kind == AIIssueSuggestion.Kind.PRIORITY:
            value = payload.get("priority")
            allowed = {choice for choice, _ in Issue.PRIORITY_CHOICES}
            if value not in allowed:
                return Response(
                    {"error": "Suggested priority is not a valid value."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            issue.priority = value
            issue.save(update_fields=["priority"])
            applied = {"priority": value}
        else:  # summary: informational only, nothing to apply on the issue
            applied = {"summary": payload.get("summary")}

        _decide(suggestion, request, AIIssueSuggestion.Status.ACCEPTED)
        log_ai_action(
            workspace=project.workspace,
            action="issue.triage_accept",
            project=project,
            actor=request.user,
            entity_type="issue",
            entity_id=issue.pk,
            status="success",
            metadata={"kind": suggestion.kind, "suggestion": str(suggestion.id), "applied": applied},
        )
        return Response(AIIssueSuggestionSerializer(suggestion).data, status=status.HTTP_200_OK)


class TriageSuggestionDismissEndpoint(BaseAPIView):
    """Dismiss a pending suggestion. Pure bookkeeping — nothing is applied."""

    def post(self, request, slug, project_id, issue_id, sid):
        project = project_member(request.user, slug, project_id)
        issue = _get_issue(issue_id, project)
        suggestion = _get_suggestion(sid, issue.pk, project)

        if suggestion.status != AIIssueSuggestion.Status.PENDING:
            return Response(
                {"error": "This suggestion has already been decided."},
                status=status.HTTP_409_CONFLICT,
            )

        _decide(suggestion, request, AIIssueSuggestion.Status.DISMISSED)
        log_ai_action(
            workspace=project.workspace,
            action="issue.triage_dismiss",
            project=project,
            actor=request.user,
            entity_type="issue",
            entity_id=issue.pk,
            status="success",
            metadata={"kind": suggestion.kind, "suggestion": str(suggestion.id)},
        )
        return Response(AIIssueSuggestionSerializer(suggestion).data, status=status.HTTP_200_OK)
