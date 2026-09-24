# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Curated serializer for real-time work item events.

The payload published to WORKITEM_EVENTS_CHANNEL is intentionally small: only
the fields the live service fans out to websocket clients and the web app
patches into its stores. No full issue objects are serialized.
"""

# Python imports
from typing import Any, Dict, List, Optional

# Django imports
from django.utils import timezone

# Module imports
from plane.db.models import Issue, IssueAssignee, IssueLabel, Workspace

# Redis channel the curated work item events are published to. The live
# service (apps/live) subscribes to this channel.
WORKITEM_EVENTS_CHANNEL = "gsd:workitem-events"


def _iso(value: Any) -> Optional[str]:
    """Serialize a date/datetime field to an ISO string (or None)."""
    return value.isoformat() if value else None


def _id(value: Any) -> Optional[str]:
    """Serialize a FK id to a string (or None)."""
    return str(value) if value else None


def serialize_workitem_event(
    issue: Issue,
    action: str,
    actor_id: Optional[str] = None,
    assignee_ids: Optional[List[str]] = None,
    label_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Build the JSON-safe payload for a work item event.

    Callers that already hold fresh assignee/label ids can pass them in to
    avoid extra queries; otherwise the current relations are read here.
    """
    if assignee_ids is None:
        assignee_ids = list(IssueAssignee.objects.filter(issue=issue).values_list("assignee_id", flat=True))
    if label_ids is None:
        label_ids = list(IssueLabel.objects.filter(issue=issue).values_list("label_id", flat=True))

    workspace_slug = (
        Workspace.objects.filter(pk=issue.workspace_id).values_list("slug", flat=True).first()
        if issue.workspace_id
        else None
    )

    state = issue.state
    state_payload = None
    if state is not None:
        state_payload = {
            "id": str(state.id),
            "name": state.name,
            "group": state.group,
            "color": state.color,
        }

    return {
        "id": str(issue.id),
        "workspace_slug": workspace_slug,
        "project_id": str(issue.project_id),
        "sequence_id": issue.sequence_id,
        "name": issue.name,
        "description_html": issue.description_html,
        "priority": issue.priority,
        "state_id": _id(issue.state_id),
        "state": state_payload,
        "assignee_ids": [str(assignee_id) for assignee_id in assignee_ids],
        "label_ids": [str(label_id) for label_id in label_ids],
        "start_date": _iso(issue.start_date),
        "target_date": _iso(issue.target_date),
        "estimate_point": _id(issue.estimate_point_id),
        "parent_id": _id(issue.parent_id),
        "sort_order": issue.sort_order,
        "completed_at": _iso(issue.completed_at),
        "archived_at": _iso(issue.archived_at),
        "actor_id": actor_id or None,
        "action": action,
        "timestamp": timezone.now().isoformat(),
    }
