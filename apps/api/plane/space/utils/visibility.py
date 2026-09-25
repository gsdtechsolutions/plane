from django.db.models import Exists, OuterRef, Q
from django.shortcuts import get_object_or_404
from plane.db.models import DeployBoard, IntakeIssue, Issue


def published_board(anchor):
    return get_object_or_404(DeployBoard, anchor=anchor, entity_name="project", is_disabled=False)


def public_issues(board):
    """Require active acceptance for feedback without multiplying issue rows.

    Any active unaccepted intake keeps the ticket private. Deleted intake history
    still marks a ticket as feedback: removing its bridge never publishes it.
    An active acceptance may publish it once no active private intake remains.
    """
    history = IntakeIssue.all_objects.filter(issue_id=OuterRef("pk"))
    active = history.filter(deleted_at__isnull=True)
    return (
        Issue.issue_objects.filter(project_id=board.project_id, workspace_id=board.workspace_id)
        .alias(
            has_intake_history=Exists(history),
            has_accepted_intake=Exists(active.filter(status=1)),
            has_private_intake=Exists(active.exclude(status=1)),
        )
        .filter(Q(has_intake_history=False) | Q(has_accepted_intake=True, has_private_intake=False))
    )
