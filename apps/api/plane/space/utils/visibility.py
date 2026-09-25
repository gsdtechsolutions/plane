from django.db.models import Q
from django.shortcuts import get_object_or_404
from plane.db.models import DeployBoard, Issue


def published_board(anchor):
    return get_object_or_404(DeployBoard, anchor=anchor, entity_name="project", is_disabled=False)


def public_issues(board):
    return Issue.issue_objects.filter(
        Q(issue_intake__isnull=True) | Q(issue_intake__status=1, issue_intake__deleted_at__isnull=True),
        project_id=board.project_id,
        workspace_id=board.workspace_id,
    )
