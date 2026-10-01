# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""GSD fork: a work item has exactly zero or one assignee (product decision).

The guard lives in IssueCreateSerializer.validate (covers create + update through the
app API) and draft.py; bulk operations clamp to the first id defensively.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from plane.db.models import Issue, Project, ProjectMember, State, User, WorkspaceMember
from plane.app.serializers.issue import IssueCreateSerializer


@pytest.fixture(autouse=True)
def isolate_external_delivery():
    # Keep scratch work items off live Redis channels and worker queues.
    with patch("plane.bgtasks.workitem_realtime._schedule_publish"), patch("celery.app.task.Task.apply_async"):
        yield


@pytest.fixture
def board(workspace, create_user):
    project = Project.objects.create(name="Single assignee", identifier="SINGLEAS", workspace=workspace)
    ProjectMember.objects.create(project=project, member=create_user, role=20, is_active=True)
    state = State.objects.create(name="Todo", group="unstarted", color="#555555", project=project)
    issue = Issue.objects.create(name="Guarded", project=project, state=state, priority="none")
    mate = User.objects.create(email="single-as-mate@example.test", username="single-as-mate")
    WorkspaceMember.objects.create(workspace=workspace, member=mate, role=15, is_active=True)
    ProjectMember.objects.create(project=project, member=mate, role=15, is_active=True)
    return SimpleNamespace(project=project, issue=issue, owner=create_user, mate=mate)


def serializer(board, data):
    return IssueCreateSerializer(
        board.issue,
        data=data,
        partial=True,
        context={"project_id": board.project.id, "workspace_id": board.project.workspace_id},
    )


def test_multiple_assignees_rejected(board):
    s = serializer(board, {"assignee_ids": [str(board.owner.id), str(board.mate.id)]})
    assert not s.is_valid()
    assert "Only one assignee is allowed per work item." in str(s.errors)


def test_single_assignee_allowed(board):
    s = serializer(board, {"assignee_ids": [str(board.mate.id)]})
    assert s.is_valid(), s.errors
    s.save()
    board.issue.refresh_from_db()
    assert list(
        board.issue.assignees.filter(issue_assignee__deleted_at__isnull=True).values_list("id", flat=True)
    ) == [board.mate.id]


def test_clearing_assignees_allowed(board):
    s = serializer(board, {"assignee_ids": []})
    assert s.is_valid(), s.errors


def test_omitting_assignees_still_valid(board):
    s = serializer(board, {"name": "Renamed without assignee change"})
    assert s.is_valid(), s.errors
