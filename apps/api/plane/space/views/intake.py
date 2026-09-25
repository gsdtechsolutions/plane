# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import json
import time

from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import serializers, status
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle

from .base import BaseViewSet
from plane.db.models import IntakeIssue, Issue, ProjectMember, State, StateGroup
from plane.space.utils.visibility import published_board
from plane.utils.content_validator import validate_html_content
from plane.bgtasks.issue_activities_task import issue_activity


class FeedbackWriteThrottle(UserRateThrottle):
    rate = "20/hour"
    scope = "public_feedback"

    def allow_request(self, request, view):
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return True
        return super().allow_request(request, view)


class FeedbackIssueInput(serializers.Serializer):
    name = serializers.CharField(max_length=255, trim_whitespace=True)
    description_html = serializers.CharField(max_length=32000, required=False, allow_blank=True, default="<p></p>")

    def to_internal_value(self, data):
        if not isinstance(data, dict) or set(data) - set(self.fields):
            raise serializers.ValidationError("Only title and description may be submitted.")
        return super().to_internal_value(data)

    def validate_description_html(self, value):
        _, _, sanitized = validate_html_content(value)
        return sanitized if sanitized is not None else "<p></p>"


class FeedbackInput(serializers.Serializer):
    feedback_type = serializers.ChoiceField(choices=("bug", "feature"))
    issue = FeedbackIssueInput()

    def to_internal_value(self, data):
        if not isinstance(data, dict) or set(data) - set(self.fields):
            raise serializers.ValidationError("Unexpected feedback fields.")
        return super().to_internal_value(data)


class FeedbackSerializer(serializers.ModelSerializer):
    name = serializers.CharField(source="issue.name")
    description_html = serializers.CharField(source="issue.description_html")
    issue_id = serializers.UUIDField(read_only=True)

    class Meta:
        model = IntakeIssue
        fields = ("id", "issue_id", "feedback_type", "name", "description_html", "status", "created_at")
        read_only_fields = fields


class IntakeIssuePublicViewSet(BaseViewSet):
    """Reporter/admin view of private feedback; public visibility is a separate accepted-only path."""

    serializer_class = FeedbackSerializer
    model = IntakeIssue
    throttle_classes = [FeedbackWriteThrottle]

    def board(self):
        board = published_board(self.kwargs["anchor"])
        if board.intake_id is None or str(board.intake_id) != str(self.kwargs["intake_id"]):
            raise NotFound("Feedback is not enabled for this board.")
        return board

    def is_project_admin(self, board):
        return ProjectMember.objects.filter(
            project_id=board.project_id,
            workspace_id=board.workspace_id,
            member_id=self.request.user.id,
            is_active=True,
            role=20,
        ).exists()

    def get_queryset(self):
        board = self.board()
        items = IntakeIssue.objects.filter(
            project_id=board.project_id, workspace_id=board.workspace_id, intake_id=board.intake_id
        ).select_related("issue")
        if not self.is_project_admin(board):
            items = items.filter(created_by=self.request.user)
        return items

    def list(self, request, anchor, intake_id):
        return Response(FeedbackSerializer(self.get_queryset().order_by("-created_at")[:100], many=True).data)

    def retrieve(self, request, anchor, intake_id, pk):
        return Response(FeedbackSerializer(get_object_or_404(self.get_queryset(), pk=pk)).data)

    @transaction.atomic
    def create(self, request, anchor, intake_id):
        board = self.board()
        # Serialize first-time triage creation for this board.
        type(board).objects.select_for_update().get(pk=board.pk)
        serializer = FeedbackInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        triage = State.triage_objects.filter(project_id=board.project_id, workspace_id=board.workspace_id).first()
        if triage is None:
            triage = State.objects.create(
                name="Triage",
                group=StateGroup.TRIAGE.value,
                is_triage=True,
                project_id=board.project_id,
                workspace_id=board.workspace_id,
                color="#4E5355",
                sequence=65000,
            )
        issue = Issue.objects.create(
            **data["issue"],
            project_id=board.project_id,
            workspace_id=board.workspace_id,
            state=triage,
            priority="none",
            created_by=request.user,
            updated_by=request.user,
        )
        record = IntakeIssue.objects.create(
            issue=issue,
            intake_id=board.intake_id,
            project_id=board.project_id,
            workspace_id=board.workspace_id,
            feedback_type=data["feedback_type"],
            created_by=request.user,
            updated_by=request.user,
        )
        transaction.on_commit(
            lambda: issue_activity.delay(
                type="issue.activity.created",
                requested_data=json.dumps({"name": issue.name, "description_html": issue.description_html}),
                actor_id=str(request.user.id),
                issue_id=str(issue.id),
                project_id=str(board.project_id),
                current_instance=None,
                epoch=int(time.time()),
            )
        )
        return Response(FeedbackSerializer(record).data, status=status.HTTP_201_CREATED)

    def editable(self, pk):
        record = get_object_or_404(self.get_queryset().select_for_update(), pk=pk)
        if record.created_by_id != self.request.user.id or record.status != -2:
            raise PermissionDenied("Only the reporter may change feedback while it is awaiting review.")
        return record

    @transaction.atomic
    def partial_update(self, request, anchor, intake_id, pk):
        record = self.editable(pk)
        serializer = FeedbackInput(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        for field, value in data.get("issue", {}).items():
            setattr(record.issue, field, value)
        record.issue.updated_by = request.user
        record.issue.save()
        if "feedback_type" in data:
            record.feedback_type = data["feedback_type"]
            record.save(update_fields=["feedback_type", "updated_at"])
        return Response(FeedbackSerializer(record).data)

    @transaction.atomic
    def destroy(self, request, anchor, intake_id, pk):
        record = self.editable(pk)
        record.issue.delete()
        record.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
