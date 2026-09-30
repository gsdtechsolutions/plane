# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""AI triage pass on work-item creation (Linear Triage Intelligence parity).

An async LLM pass proposes labels (existing project labels only), an assignee
(project members only), a priority (known values only) and a one-line summary.
Everything lands in AIIssueSuggestion as REVIEWABLE chips on the issue detail —
never silent writes. The LLM output is parsed defensively: any garbage, fenced
prose or provider failure degrades to an audited error, not a crash."""

import json
import logging
import re

from celery import shared_task
from django.utils import timezone

# NOTE: no plane.db / plane.app model imports at module scope — this module is
# imported from plane/celery.py before Django apps finish loading (same contract
# as slack_delivery.tasks / github_delivery.tasks). Model and provider imports
# are deferred into the task body.

logger = logging.getLogger(__name__)

DESCRIPTION_CAP = 6000
ALLOWED_PRIORITIES = ("urgent", "high", "medium", "low")
INSTRUCTIONS_CAP = 2000

TRIAGE_INSTRUCTIONS = (
    "Triage a newly created work item. Suggest at most 2 labels chosen ONLY from the provided existing label names "
    "(empty list when none fit), an assignee chosen ONLY from the provided project member emails (null when none fit), "
    "a priority ONLY from urgent|high|medium|low (null when unsure), and a one-line summary of at most 160 characters. "
    "Respond with STRICT JSON only — no prose, no markdown fences — shaped exactly as: "
    '{"labels": ["<existing label name>"], "assignee_email": "<member email>"|null, '
    '"priority": "urgent"|"high"|"medium"|"low"|null, "summary": "<=160 chars", "confidence": 0.0-1.0}. '
    "Never invent labels, emails, or priority values outside the provided options."
)


def _parse_suggestions(text):
    """Defensively pull the first JSON object out of a model response.

    Strips markdown fences and surrounding prose, then raw-decodes the first
    {...} block. Raises ValueError on anything that is not a JSON object."""
    cleaned = (text or "").strip()
    cleaned = re.sub(r"```(?:json)?", "", cleaned)
    start = cleaned.find("{")
    if start == -1:
        raise ValueError("No JSON object found in model response.")
    decoded, _ = json.JSONDecoder().raw_decode(cleaned[start:])
    if not isinstance(decoded, dict):
        raise ValueError("Model response JSON is not an object.")
    return decoded


def _clamp_confidence(value):
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(1.0, confidence))


def _member_directory(project):
    """Active project members as (email, display name, user id) tuples."""
    from plane.db.models import ProjectMember

    members = []
    for row in (
        ProjectMember.objects.filter(project=project, is_active=True, member__isnull=False)
        .select_related("member")
        .order_by("member__email")
    ):
        members.append((row.member.email, getattr(row.member, "display_name", "") or row.member.email, str(row.member_id)))
    return members


@shared_task(name="ai_triage.triage_issue")
def triage_issue(issue_id):
    from plane.app.ai_ops.service import log_ai_action
    from plane.app.release_intelligence.provider import IntelligenceError, generate_text
    from plane.db.models import Issue, Label
    from plane.db.models.ai_triage import AIIssueSuggestion

    issue = (
        Issue.objects.filter(pk=issue_id)
        .select_related("project", "workspace", "state")
        .prefetch_related("issue_assignee__assignee")
        .first()
    )
    if issue is None:
        return

    if AIIssueSuggestion.objects.filter(issue=issue, status=AIIssueSuggestion.Status.PENDING).exists():
        return

    project = issue.project
    workspace = issue.workspace
    started_at = timezone.now()

    try:
        label_names = list(
            Label.objects.filter(project=project, deleted_at__isnull=True)
            .order_by("name")
            .values_list("name", flat=True)
        )
        members = _member_directory(project)

        sources = [
            {"title": "Work item title", "content": issue.name or ""},
            {
                "title": "Work item description",
                "content": (issue.description_stripped or "")[:DESCRIPTION_CAP],
            },
            {"title": "Current state", "content": issue.state.name if issue.state else "No state"},
        ]
        context = (
            "Existing labels in this project (use these names verbatim, at most 2): "
            + json.dumps(label_names)
            + ". Project members (assignee_email must be one of these emails): "
            + json.dumps([{"email": email, "name": name} for email, name, _ in members])
            + ". Allowed priority values: urgent, high, medium, low (null when none applies)."
        )
        result = generate_text(project, sources, instructions=(context + "\n" + TRIAGE_INSTRUCTIONS)[:INSTRUCTIONS_CAP])
        parsed = _parse_suggestions(result.get("text", ""))
    except (IntelligenceError, ValueError, json.JSONDecodeError) as exc:
        log_ai_action(
            workspace=workspace,
            action="issue.triage_suggest",
            project=project,
            entity_type="issue",
            entity_id=issue.pk,
            status="error",
            input_excerpt=issue.name or "",
            error=str(exc) or exc.__class__.__name__,
            latency_ms=int((timezone.now() - started_at).total_seconds() * 1000),
        )
        return

    model = result.get("model", "") or ""
    confidence = _clamp_confidence(parsed.get("confidence"))
    created = 0

    # Labels: case-insensitive matches to EXISTING project labels only. Never invent.
    known_labels = {name.lower(): name for name in label_names}
    seen_labels = set()
    for name in (parsed.get("labels") or [])[:2]:
        if not isinstance(name, str):
            continue
        canonical = known_labels.get(name.strip().lower())
        if not canonical or canonical.lower() in seen_labels:
            continue
        seen_labels.add(canonical.lower())
        AIIssueSuggestion.objects.create(
            workspace=workspace,
            project=project,
            issue=issue,
            kind=AIIssueSuggestion.Kind.LABEL,
            payload={"name": canonical},
            confidence=confidence,
            model=model,
        )
        created += 1

    # Assignee: only when the email belongs to an active project member.
    email = parsed.get("assignee_email")
    if isinstance(email, str) and email.strip():
        email = email.strip().lower()
        for member_email, _, member_id in members:
            if member_email.lower() == email:
                AIIssueSuggestion.objects.create(
                    workspace=workspace,
                    project=project,
                    issue=issue,
                    kind=AIIssueSuggestion.Kind.ASSIGNEE,
                    payload={"user_id": member_id, "email": member_email},
                    confidence=confidence,
                    model=model,
                )
                created += 1
                break

    # Priority: only allowed values.
    priority = parsed.get("priority")
    if isinstance(priority, str) and priority.strip().lower() in ALLOWED_PRIORITIES:
        AIIssueSuggestion.objects.create(
            workspace=workspace,
            project=project,
            issue=issue,
            kind=AIIssueSuggestion.Kind.PRIORITY,
            payload={"priority": priority.strip().lower()},
            confidence=confidence,
            model=model,
        )
        created += 1

    # Summary: informational, always recorded when present.
    summary = parsed.get("summary")
    if isinstance(summary, str) and summary.strip():
        AIIssueSuggestion.objects.create(
            workspace=workspace,
            project=project,
            issue=issue,
            kind=AIIssueSuggestion.Kind.SUMMARY,
            payload={"summary": summary.strip()[:160]},
            confidence=confidence,
            model=model,
        )
        created += 1

    log_ai_action(
        workspace=workspace,
        action="issue.triage_suggest",
        project=project,
        entity_type="issue",
        entity_id=issue.pk,
        model=model,
        status="success",
        input_excerpt=issue.name or "",
        output_excerpt=json.dumps(parsed)[:2000],
        latency_ms=int((timezone.now() - started_at).total_seconds() * 1000),
        metadata={"suggestions": created},
    )


def maybe_enqueue_triage(issue_id, created=False):
    """Coordinator call-site helper: enqueue the triage pass for new issues only."""
    if created:
        triage_issue.delay(issue_id)
