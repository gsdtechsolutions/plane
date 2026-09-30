# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Workspace Q&A: search the workspace's issues and comments, then answer
the natural-language question with the LLM using [KEY-n] citations.

Synchronous by design: web fetches wait, the provider call is a single
short completion, so no celery hop is involved."""

import re

from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from plane.app.ai_ops.service import log_ai_action
from plane.app.release_intelligence.provider import IntelligenceError, generate_text
from plane.app.views.base import BaseAPIView
from plane.db.models import Issue, Workspace, WorkspaceMember

MAX_RESULTS = 8
MAX_TOKENS = 6
MAX_QUESTION_LENGTH = 500
MIN_QUESTION_LENGTH = 3

STOPWORDS = frozenset(
    """a an about and are as at be been do does did for from has have how i in is it
    its me my of on or our that the their there they this to was we were what when
    where which who why will with you your""".split()
)

ANSWER_INSTRUCTIONS = (
    "Answer the user's question using ONLY the provided issue sources. "
    "Cite claims with [KEY-n] markers matching the source blocks. "
    "If the sources do not contain the answer, say so plainly. "
    "Maximum 180 words. Plain text, no markdown headers."
)


def workspace_member(user, slug):
    """Any active workspace member may ask questions about the workspace."""
    workspace = get_object_or_404(Workspace, slug=slug)
    if (
        not user.is_authenticated
        or not WorkspaceMember.objects.filter(workspace=workspace, member=user, is_active=True).exists()
    ):
        raise PermissionDenied("Only workspace members can ask questions.")
    return workspace


def tokenize(question):
    """Lowercase keyword tokens for the issue search; stopwords and very
    short filler are dropped, at most MAX_TOKENS distinct tokens are kept.
    Digit-bearing tokens (issue-key fragments like "286") always survive."""
    tokens = []
    for raw in re.split(r"[^a-z0-9]+", question.lower()):
        if len(raw) < 3 or raw in STOPWORDS or raw in tokens:
            if not (raw and any(ch.isdigit() for ch in raw)):
                continue
        if raw in tokens:
            continue
        tokens.append(raw)
        if len(tokens) >= MAX_TOKENS:
            break
    return tokens


def key_issue_matches(workspace_slug, question, project_id=None):
    """Direct matches for work item keys written like FR-12 or AEK286 — the
    display key lives outside name/description, so token search cannot find it."""
    pairs = re.findall(r"\b([a-zA-Z]{2,8})-?\s?(\d{1,6})\b", question)
    if not pairs:
        return Issue.objects.none()
    queryset = Issue.objects.filter(workspace__slug=workspace_slug, archived_at__isnull=True)
    if project_id:
        queryset = queryset.filter(project_id=project_id)
    match = None
    for prefix, number in pairs:
        clause = Q(project__identifier__iexact=prefix, sequence_id=int(number))
        match = clause if match is None else match | clause
    return queryset.filter(match).select_related("state", "project").prefetch_related("issue_assignee__assignee")[:MAX_RESULTS]


def search_issues(workspace_slug, tokens, project_id=None):
    """Issues matching any token in the title, stripped description, or a
    comment body; comments join in via OR so hits never require both."""
    if not tokens:
        return Issue.objects.none()
    queryset = Issue.objects.filter(workspace__slug=workspace_slug, archived_at__isnull=True)
    if project_id:
        queryset = queryset.filter(project_id=project_id)
    match = None
    for token in tokens:
        clause = (
            Q(name__icontains=token)
            | Q(description_stripped__icontains=token)
            | Q(issue_comments__comment_stripped__icontains=token)
        )
        match = clause if match is None else match | clause
    return (
        queryset.filter(match)
        .distinct()
        .order_by("-updated_at")
        .select_related("state", "project")
        .prefetch_related("issue_assignee__assignee")[:MAX_RESULTS]
    )


def display_key(issue, n):
    """KEY-n marker: the project-unique display key plus the source index."""
    identifier = (issue.project.identifier or "").strip()
    key = f"{identifier}-{issue.sequence_id}" if identifier else f"#{issue.sequence_id}"
    return f"{key}-{n}"


class WorkspaceAskEndpoint(BaseAPIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, slug):
        workspace = workspace_member(request.user, slug)
        question = request.data.get("question")
        if not isinstance(question, str) or not MIN_QUESTION_LENGTH <= len(question.strip()) <= MAX_QUESTION_LENGTH:
            raise ValidationError(f"Ask a question between {MIN_QUESTION_LENGTH} and {MAX_QUESTION_LENGTH} characters.")
        question = question.strip()
        tokens = tokenize(question)
        key_hits = list(key_issue_matches(workspace.slug, question, request.data.get("project_id")))
        if not tokens and not key_hits:
            raise ValidationError("Try asking with more specific words, or use a work item key like FR-12.")

        project_id = request.data.get("project_id")
        if project_id is not None and not isinstance(project_id, str):
            raise ValidationError("project_id must be an id.")
        if project_id:
            key_hits = [issue for issue in key_hits if str(issue.project_id) == project_id]

        issues = list(key_hits)
        seen_ids = {issue.id for issue in issues}
        for issue in search_issues(workspace.slug, tokens, project_id):
            if issue.id not in seen_ids:
                issues.append(issue)
                seen_ids.add(issue.id)
        issues = issues[:MAX_RESULTS]
        if not issues:
            log_ai_action(
                workspace=workspace,
                action="workspace.ask",
                actor=request.user,
                entity_type="workspace",
                entity_id=workspace.id,
                output_excerpt="no_matches",
            )
            return Response(
                {"answer": None, "references": [], "message": "No matching issues found. Try different words, or a work item key like FR-12."}
            )

        references = []
        sources = []
        for n, issue in enumerate(issues, start=1):
            display = display_key(issue, n)
            assignees = [row.assignee for row in issue.issue_assignee.all() if row.assignee]
            emails = ", ".join(assignee.email for assignee in assignees) or "none"
            block = (
                f"[{display}] {issue.name} | state {getattr(issue.state, 'name', 'None')} "
                f"| priority {issue.priority or 'none'} | assignees {emails} | {(issue.description_stripped or '')[:500]}"
            )
            references.append(
                {
                    "id": str(issue.id),
                    "name": issue.name,
                    "display": display,
                    "state": getattr(issue.state, "name", None),
                    "state_group": getattr(issue.state, "group", None),
                    "priority": issue.priority,
                    "url": f"/{workspace.slug}/projects/{issue.project_id}/issues/{issue.id}",
                }
            )
            sources.append({"title": issue.name[:500], "content": block})

        try:
            result = generate_text(issues[0].project, sources, ANSWER_INSTRUCTIONS, review=False)
        except IntelligenceError:
            log_ai_action(
                workspace=workspace,
                action="workspace.ask",
                project=issues[0].project,
                actor=request.user,
                entity_type="workspace",
                entity_id=workspace.id,
                status="error",
                input_excerpt=question,
                error="ai_unconfigured",
            )
            return Response({"error": "ai_unconfigured"}, status=503)

        answer = result.get("text", "")
        log_ai_action(
            workspace=workspace,
            action="workspace.ask",
            project=issues[0].project,
            actor=request.user,
            entity_type="workspace",
            entity_id=workspace.id,
            model=result.get("model", ""),
            input_excerpt=question,
            output_excerpt=answer,
        )
        return Response({"answer": answer, "model": result.get("model"), "references": references, "message": None})
