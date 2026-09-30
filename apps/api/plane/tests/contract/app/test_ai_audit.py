# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
import uuid
from unittest.mock import patch

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from plane.db.models import Project, ProjectMember, User, Workspace, WorkspaceMember
from plane.db.models.ai_audit import AIActionAudit
from plane.app.ai_ops.service import log_ai_action

pytestmark = [pytest.mark.contract, pytest.mark.django_db(transaction=True)]


@pytest.fixture
def setup(create_user):
    workspace = Workspace.objects.create(name="AI Ops", slug="ai-ops", owner=create_user)
    WorkspaceMember.objects.create(workspace=workspace, member=create_user, role=20, is_active=True)
    project = Project.objects.create(name="AI", identifier="AI", workspace=workspace)
    ProjectMember.objects.create(project=project, member=create_user, role=20, is_active=True)
    member = User.objects.create(email="member@ai-ops.test", username="member", password="x")
    WorkspaceMember.objects.create(workspace=workspace, member=member, role=5, is_active=True)
    return workspace, project, create_user, member


def test_log_ai_action_records_row(setup):
    workspace, project, user, _ = setup
    row = log_ai_action(
        workspace=workspace,
        action="issue.summarize",
        project=project,
        actor=user,
        entity_type="issue",
        entity_id=uuid.uuid4(),
        model="openai/deepseek-chat",
        input_excerpt="Summarize FR-14",
        output_excerpt="Done" * 5000,
        latency_ms=812,
        metadata={"channel": "C123", "thread_ts": "1690000000.1"},
    )
    assert row is not None
    assert row.status == AIActionAudit.Status.SUCCESS
    assert len(row.output_excerpt) == 8000  # defensively truncated
    assert row.metadata["channel"] == "C123"


def test_log_ai_action_records_error_status(setup):
    workspace, _, user, _ = setup
    row = log_ai_action(
        workspace=workspace,
        action="workspace.ask",
        actor=user,
        status=AIActionAudit.Status.ERROR,
        error="IntelligenceError: provider unavailable",
    )
    assert row is not None
    assert row.status == "error"
    assert "IntelligenceError" in row.error


def test_log_ai_action_never_raises(setup):
    workspace, _, _, _ = setup
    with patch.object(AIActionAudit.objects, "create", side_effect=RuntimeError("db down")):
        row = log_ai_action(workspace=workspace, action="issue.triage_suggest")
    assert row is None


def test_audit_endpoint_requires_workspace_admin(setup):
    workspace, _, admin, member = setup
    log_ai_action(workspace=workspace, action="issue.summarize", actor=admin)
    client = APIClient()
    client.force_authenticate(user=member)
    response = client.get(f"/api/workspaces/{workspace.slug}/ai-audit/")
    assert response.status_code == 403


def test_audit_endpoint_lists_and_filters(setup):
    workspace, project, admin, _ = setup
    log_ai_action(workspace=workspace, action="issue.summarize", project=project, actor=admin, output_excerpt="ok")
    log_ai_action(workspace=workspace, action="agent.delegate", actor=admin)
    other = User.objects.create(email="admin2@ai-ops.test", username="admin2", password="x")
    WorkspaceMember.objects.create(workspace=workspace, member=other, role=20, is_active=True)

    client = APIClient()
    client.force_authenticate(user=other)
    response = client.get(f"/api/workspaces/{workspace.slug}/ai-audit/")
    assert response.status_code == 200
    assert response.data["count"] == 2

    filtered = client.get(f"/api/workspaces/{workspace.slug}/ai-audit/", {"action": "agent.delegate"})
    assert filtered.data["count"] == 1
    assert filtered.data["results"][0]["action"] == "agent.delegate"

    searched = client.get(f"/api/workspaces/{workspace.slug}/ai-audit/", {"search": "summarize"})
    assert searched.data["count"] == 1


def test_audit_row_orders_newest_first(setup):
    workspace, _, admin, _ = setup
    first = log_ai_action(workspace=workspace, action="issue.summarize", actor=admin)
    second = log_ai_action(workspace=workspace, action="workspace.ask", actor=admin)
    assert AIActionAudit.objects.first().id == second.id
    assert list(AIActionAudit.objects.all()) == [second, first]
