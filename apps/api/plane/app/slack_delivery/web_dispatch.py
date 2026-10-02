# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only

"""Issue-view agent dispatch: start, watch, answer and cancel runs from the
Plane web UI. Web-origin jobs carry no Slack surface — events land in the
link's event_log (rendered by the dispatch card) and job.completed still
comments on the ticket and moves it to the review state."""

from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from plane.app.views.base import BaseAPIView
from plane.db.models import Issue
from plane.db.models.slack_delivery import SlackAgentJob
from . import agent_dispatch, commands, services
from .api import project_member

INSTRUCTIONS_MAX = agent_dispatch.INSTRUCTIONS_MAX
MESSAGE_MAX = 4000
LIST_LIMIT = 10


def run_data(link):
    """Serializer for the dispatch card (web-origin transcript included)."""
    return {
        "id": str(link.id),
        "origin": link.origin,
        "status": link.status,
        "instructions": link.instructions,
        "job_id": link.job_id,
        "requester": commands.member_display(link.requester) if link.requester else "",
        "preview": link.preview or {},
        "events": link.event_log or [],
        "created_at": services.iso(link.created_at),
        "updated_at": services.iso(link.updated_at),
    }


class AgentDispatchEndpoint(BaseAPIView):
    """POST dispatch this issue to an agent; GET the issue's runs."""

    def issue(self, request, slug, project_id, issue_id):
        project = project_member(request.user, slug, project_id)
        return get_object_or_404(Issue, id=issue_id, project=project, workspace_id=project.workspace_id)

    def get(self, request, slug, project_id, issue_id):
        issue = self.issue(request, slug, project_id, issue_id)
        runs = (
            SlackAgentJob.objects.filter(issue=issue)
            .select_related("requester")
            .order_by("-created_at")[:LIST_LIMIT]
        )
        return Response([run_data(link) for link in runs])

    def post(self, request, slug, project_id, issue_id):
        issue = self.issue(request, slug, project_id, issue_id)
        instructions = request.data.get("instructions")
        if instructions is None:
            instructions = ""
        if not isinstance(instructions, str):
            raise ValidationError("Instructions must be text.")
        if len(instructions) > INSTRUCTIONS_MAX:
            raise ValidationError(f"Instructions are limited to {INSTRUCTIONS_MAX} characters.")
        try:
            link, created = agent_dispatch.start_from_web(issue, request.user, instructions)
        except commands.CommandError as error:
            return Response({"error": str(error)}, status=status.HTTP_400_BAD_REQUEST)
        # The worker enqueues on transaction commit inside register_job.
        return Response(run_data(link), status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class AgentDispatchMessageEndpoint(BaseAPIView):
    """POST a human reply into a running job (answers open questions; extra
    text is queued for the agent's next check)."""

    def post(self, request, slug, project_id, issue_id, run_id):
        project = project_member(request.user, slug, project_id)
        issue = get_object_or_404(Issue, id=issue_id, project=project, workspace_id=project.workspace_id)
        link = get_object_or_404(SlackAgentJob, id=run_id, issue=issue)
        if link.status in agent_dispatch.TERMINAL_STATUSES:
            return Response({"error": agent_dispatch.JOB_FINISHED_TEXT}, status=status.HTTP_409_CONFLICT)
        text = request.data.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ValidationError("Message text is required.")
        text = text.strip()[:MESSAGE_MAX]
        if not link.job_id:
            raise ValidationError("The agent run has not started yet.")
        try:
            agent_dispatch.post_job_message(
                link.job_id,
                {
                    "slack_user_id": link.requester_slack_user_id if request.user.id == link.requester_id else "",
                    "plane_user_id": str(request.user.id),
                    "text": text,
                    "ts": "",
                },
            )
        except agent_dispatch.DispatchConflict:
            agent_dispatch.mark_completed_from_conflict(link)
            link.refresh_from_db()
            return Response({"error": agent_dispatch.JOB_FINISHED_TEXT}, status=status.HTTP_409_CONFLICT)
        except agent_dispatch.DispatchUnavailable as error:
            return Response(
                {"error": "Plane could not reach the dispatcher. Try again."},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        link.event_log = (list(link.event_log or []) + [{
            "id": "",
            "type": "message.sent",
            "at": timezone.now().isoformat(),
            "text": text[:2000],
            "by": commands.member_display(request.user),
        }])[-50:]
        link.save(update_fields=["event_log", "updated_at"])
        return Response(run_data(link))


class AgentDispatchCancelEndpoint(BaseAPIView):
    """POST cancel a running dispatch (idempotent once terminal)."""

    def post(self, request, slug, project_id, issue_id, run_id):
        project = project_member(request.user, slug, project_id)
        issue = get_object_or_404(Issue, id=issue_id, project=project, workspace_id=project.workspace_id)
        link = get_object_or_404(SlackAgentJob, id=run_id, issue=issue)
        if link.status in agent_dispatch.TERMINAL_STATUSES:
            return Response(run_data(link))
        if link.job_id:
            try:
                agent_dispatch.cancel_job(link.job_id)
            except agent_dispatch.DispatchConflict:
                agent_dispatch.mark_completed_from_conflict(link)
                link.refresh_from_db()
                return Response(run_data(link))
            except agent_dispatch.DispatchUnavailable:
                return Response(
                    {"error": "Plane could not reach the dispatcher. Try again."},
                    status=status.HTTP_502_BAD_GATEWAY,
                )
        link.status = "cancelled"
        link.save(update_fields=["status", "updated_at"])
        return Response(run_data(link))
