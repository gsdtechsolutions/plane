# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Contract tests for the MCP endpoint (POST /mcp/).

Covers protocol framing (initialize, notifications, batch, errors) and
every tool against seeded ORM data, using the same X-API-Key /
Bearer-token authentication as the external REST API.
"""

# Python imports
import json
import time

# Third party imports
import pytest

# Django imports
from django.core.cache import cache

# Module imports
from plane.db.models import (
    APIToken,
    Issue,
    IssueAssignee,
    IssueComment,
    IssueLabel,
    Label,
    Project,
    ProjectMember,
    State,
    User,
    Workspace,
    WorkspaceMember,
)

MCP_URL = "/mcp/"
TOKEN = "test-api-token-12345"


# ------------------------------------------------------------------ helpers


def rpc(method, params=None, message_id=1):
    return {"jsonrpc": "2.0", "id": message_id, "method": method, "params": params or {}}


def post_rpc(client, payload):
    return client.post(MCP_URL, payload, format="json")


def call_tool(client, name, arguments, message_id=1):
    return post_rpc(client, rpc("tools/call", {"name": name, "arguments": arguments}, message_id))


def tool_payload(response):
    """Return (payload, is_error) for a tools/call response."""
    body = response.json()
    result = body["result"]
    text = result["content"][0]["text"]
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        parsed = text
    return parsed, result.get("isError", False)


@pytest.fixture
def workspace_member_user(create_user):
    return create_user


@pytest.fixture
def mcp_workspace(create_user, workspace_member_user):
    workspace = Workspace.objects.create(
        name="MCP Workspace", slug="mcp-workspace", owner=create_user
    )
    WorkspaceMember.objects.create(
        workspace=workspace, member=workspace_member_user, role=20, is_active=True
    )
    return workspace


@pytest.fixture
def project(mcp_workspace, workspace_member_user):
    project = Project.objects.create(
        name="MCP Test Project",
        identifier="MCPX",
        workspace=mcp_workspace,
        created_by=workspace_member_user,
    )
    ProjectMember.objects.create(
        workspace=mcp_workspace, project=project, member=workspace_member_user, role=20, is_active=True
    )
    return project


@pytest.fixture
def states(project, mcp_workspace, workspace_member_user):
    backlog = State.objects.create(
        name="Backlog", group="backlog", project=project, workspace=mcp_workspace, default=True
    )
    started = State.objects.create(
        name="In Progress", group="started", project=project, workspace=mcp_workspace
    )
    done = State.objects.create(
        name="Done", group="completed", project=project, workspace=mcp_workspace
    )
    return {"backlog": backlog, "started": started, "done": done}


@pytest.fixture
def seeded_issue(project, mcp_workspace, states, workspace_member_user):
    return Issue.objects.create(
        name="Seeded issue",
        description_html="<p>Original description</p>",
        priority="high",
        state=states["started"],
        project=project,
        workspace=mcp_workspace,
        created_by=workspace_member_user,
    )


# ------------------------------------------------------------------ protocol


@pytest.mark.contract
@pytest.mark.django_db
def test_mcp_requires_auth(api_client):
    response = post_rpc(api_client, rpc("tools/list"))
    assert response.status_code == 401


@pytest.mark.contract
@pytest.mark.django_db
def test_initialize_with_x_api_key(api_key_client):
    response = post_rpc(api_key_client, rpc("initialize", {"protocolVersion": "2025-06-18"}))
    assert response.status_code == 200
    result = response.json()["result"]
    assert result["protocolVersion"] == "2025-06-18"
    assert result["serverInfo"]["name"] == "plane-mcp"
    assert "tools" in result["capabilities"]
    assert "Plane" in result["instructions"]


@pytest.mark.contract
@pytest.mark.django_db
def test_initialize_with_bearer_token(api_key_client):
    api_key_client.credentials(HTTP_AUTHORIZATION=f"Bearer {TOKEN}")
    response = post_rpc(api_key_client, rpc("initialize", {"protocolVersion": "2025-03-26"}))
    assert response.status_code == 200
    assert response.json()["result"]["protocolVersion"] == "2025-03-26"


@pytest.mark.contract
@pytest.mark.django_db
def test_initialize_negotiates_unknown_version(api_key_client):
    response = post_rpc(api_key_client, rpc("initialize", {"protocolVersion": "1999-01-01"}))
    assert response.json()["result"]["protocolVersion"] == "2025-11-25"


@pytest.mark.contract
@pytest.mark.django_db
def test_mcp_is_not_rate_limited(api_key_client, mcp_workspace):
    # Saturate the per-key ApiKeyRateThrottle bucket kept in the cache.
    now = time.time()
    cache.set(f"api_key:{TOKEN}", [now] * 60, timeout=120)
    try:
        # The REST surface still throttles this key…
        rest = api_key_client.get(f"/api/v1/workspaces/{mcp_workspace.slug}/projects/")
        assert rest.status_code == 429
        # …while /mcp answers normally for the very same key.
        response = post_rpc(api_key_client, rpc("tools/list"))
        assert response.status_code == 200
        assert "tools" in response.json()["result"]
    finally:
        cache.delete(f"api_key:{TOKEN}")


@pytest.mark.contract
@pytest.mark.django_db
def test_initialized_notification_returns_202(api_key_client):
    response = post_rpc(api_key_client, rpc("notifications/initialized", message_id=None))
    assert response.status_code == 202
    assert response.content == b""


@pytest.mark.contract
@pytest.mark.django_db
def test_ping(api_key_client):
    response = post_rpc(api_key_client, rpc("ping"))
    assert response.status_code == 200
    assert response.json()["result"] == {}


@pytest.mark.contract
@pytest.mark.django_db
def test_unknown_method_returns_error(api_key_client):
    response = post_rpc(api_key_client, rpc("some/futureMethod"))
    assert response.status_code == 200
    assert response.json()["error"]["code"] == -32601


@pytest.mark.contract
@pytest.mark.django_db
def test_invalid_json_returns_parse_error(api_key_client):
    response = api_key_client.post(
        MCP_URL, "{not json", content_type="application/json"
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == -32700


@pytest.mark.contract
@pytest.mark.django_db
def test_batch_messages_get_batch_response(api_key_client):
    payload = [
        rpc("ping", message_id=1),
        rpc("notifications/initialized", message_id=None),
        rpc("tools/list", message_id=2),
    ]
    response = post_rpc(api_key_client, payload)
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, list)
    assert [message["id"] for message in body] == [1, 2]


@pytest.mark.contract
@pytest.mark.django_db
def test_get_method_not_allowed(api_key_client):
    response = api_key_client.get(MCP_URL)
    assert response.status_code == 405


# --------------------------------------------------------------- tools/list


@pytest.mark.contract
@pytest.mark.django_db
def test_tools_list(api_key_client):
    response = post_rpc(api_key_client, rpc("tools/list"))
    tools = response.json()["result"]["tools"]
    names = {tool["name"] for tool in tools}
    assert names == {
        "plane_list_projects",
        "plane_list_states",
        "plane_list_members",
        "plane_list_labels",
        "plane_list_issues",
        "plane_get_issue",
        "plane_create_issue",
        "plane_update_issue",
        "plane_add_comment",
        "plane_delete_issue",
    }
    for tool in tools:
        assert tool["inputSchema"]["type"] == "object"
        assert tool["description"]


# ------------------------------------------------------------- read tools


@pytest.mark.contract
@pytest.mark.django_db
def test_list_projects(api_key_client, mcp_workspace, project):
    payload, is_error = tool_payload(
        call_tool(api_key_client, "plane_list_projects", {})
    )
    assert not is_error
    assert payload["count"] == 1
    assert payload["projects"][0]["identifier"] == "MCPX"
    assert payload["projects"][0]["name"] == "MCP Test Project"


@pytest.mark.contract
@pytest.mark.django_db
def test_list_states(api_key_client, mcp_workspace, project, states):
    payload, is_error = tool_payload(
        call_tool(api_key_client, "plane_list_states", {"project": "MCPX"})
    )
    assert not is_error
    names = {state["name"] for state in payload["states"]}
    assert names == {"Backlog", "In Progress", "Done"}
    default_states = [state for state in payload["states"] if state["is_default"]]
    assert [state["name"] for state in default_states] == ["Backlog"]


@pytest.mark.contract
@pytest.mark.django_db
def test_list_members(api_key_client, mcp_workspace, workspace_member_user):
    payload, is_error = tool_payload(
        call_tool(api_key_client, "plane_list_members", {})
    )
    assert not is_error
    assert payload["count"] == 1
    assert payload["members"][0]["display_name"] == workspace_member_user.display_name


@pytest.mark.contract
@pytest.mark.django_db
def test_get_issue_by_ref(api_key_client, mcp_workspace, project, states, seeded_issue):
    IssueComment.objects.create(
        issue=seeded_issue,
        project=project,
        workspace=mcp_workspace,
        comment_html="<p>First comment</p>",
    )
    payload, is_error = tool_payload(
        call_tool(api_key_client, "plane_get_issue", {"issue": "MCPX-1"})
    )
    assert not is_error
    assert payload["ref"] == "MCPX-1"
    assert payload["name"] == "Seeded issue"
    assert payload["state"] == "In Progress"
    assert payload["priority"] == "high"
    assert payload["description_text"] == "Original description"
    assert payload["comments"][0]["text"] == "First comment"
    assert payload["url"] and "issues" in payload["url"]


@pytest.mark.contract
@pytest.mark.django_db
def test_list_issues_excludes_closed_by_default(api_key_client, mcp_workspace, project, states):
    Issue.objects.create(name="Open item", project=project, workspace=mcp_workspace, state=states["backlog"])
    Issue.objects.create(name="Closed item", project=project, workspace=mcp_workspace, state=states["done"])

    payload, is_error = tool_payload(call_tool(api_key_client, "plane_list_issues", {}))
    assert not is_error
    assert payload["total"] == 1
    assert payload["results"][0]["name"] == "Open item"

    payload, is_error = tool_payload(
        call_tool(api_key_client, "plane_list_issues", {"include_closed": True})
    )
    assert payload["total"] == 2


@pytest.mark.contract
@pytest.mark.django_db
def test_list_issues_filters(api_key_client, mcp_workspace, project, states, workspace_member_user):
    issue = Issue.objects.create(
        name="Searchable needle",
        description_html="<p>findme text</p>",
        priority="high",
        project=project,
        workspace=mcp_workspace,
        state=states["started"],
    )
    IssueAssignee.objects.create(
        issue=issue,
        assignee=workspace_member_user,
        project=project,
        workspace=mcp_workspace,
    )
    Issue.objects.create(
        name="Other item", project=project, workspace=mcp_workspace, state=states["backlog"]
    )

    for arguments in [
        {"state": "in progress"},
        {"state_group": "started"},
        {"priority": "high"},
        {"assignee": workspace_member_user.email},
        {"search": "needle"},
        {"search": "findme"},
    ]:
        payload, is_error = tool_payload(call_tool(api_key_client, "plane_list_issues", arguments))
        assert not is_error, arguments
        assert payload["total"] == 1, arguments
        assert payload["results"][0]["name"] == "Searchable needle", arguments


@pytest.mark.contract
@pytest.mark.django_db
def test_list_issues_pagination(api_key_client, mcp_workspace, project, states):
    for index in range(3):
        Issue.objects.create(name=f"Paged {index}", project=project, workspace=mcp_workspace)

    payload, is_error = tool_payload(
        call_tool(api_key_client, "plane_list_issues", {"project": "MCPX", "limit": 2, "offset": 0})
    )
    assert not is_error, payload
    assert payload["count"] == 2 and payload["total"] == 3 and payload["offset"] == 0

    payload, is_error = tool_payload(
        call_tool(api_key_client, "plane_list_issues", {"project": "MCPX", "limit": 2, "offset": 2})
    )
    assert not is_error, payload
    assert payload["count"] == 1

    payload, is_error = tool_payload(
        call_tool(api_key_client, "plane_list_issues", {"project": "MCPX", "limit": 1000})
    )
    assert not is_error, payload
    assert payload["limit"] == 100  # clamped


@pytest.mark.contract
@pytest.mark.django_db
def test_unknown_project_returns_tool_error(api_key_client, mcp_workspace):
    payload, is_error = tool_payload(
        call_tool(api_key_client, "plane_list_states", {"project": "NOPE"})
    )
    assert is_error
    assert "not found" in payload


# ------------------------------------------------------------ write tools


@pytest.mark.contract
@pytest.mark.django_db
def test_create_issue_full(api_key_client, mcp_workspace, project, states, workspace_member_user):
    teammate = User.objects.create(
        email="teammate@plane.so", username="teammate", first_name="Team", last_name="Mate"
    )
    WorkspaceMember.objects.create(
        workspace=mcp_workspace, member=teammate, role=15, is_active=True
    )

    payload, is_error = tool_payload(
        call_tool(
            api_key_client,
            "plane_create_issue",
            {
                "project": "MCPX",
                "name": "New agent issue",
                "description_text": "Created via MCP",
                "priority": "urgent",
                "state": "in progress",
                "assignees": [teammate.email, "me"],
                "labels": ["bug", "from-agent"],
            },
        )
    )
    assert not is_error, payload
    assert payload["ref"] == "MCPX-1"
    assert payload["priority"] == "urgent"
    assert payload["state"] == "In Progress"
    assert payload["description_text"] == "Created via MCP"
    assert sorted(a["email"] for a in payload["assignees"]) == sorted(
        [teammate.email, workspace_member_user.email]
    )
    assert sorted(payload["labels"]) == ["bug", "from-agent"]

    issue = Issue.objects.get(project=project, sequence_id=1)
    assert issue.description_stripped == "Created via MCP"
    assert issue.assignees.count() == 2
    assert issue.labels.count() == 2
    assert Label.objects.filter(project=project, name="from-agent").exists()


@pytest.mark.contract
@pytest.mark.django_db
def test_create_issue_defaults(api_key_client, mcp_workspace, project, states):
    payload, is_error = tool_payload(
        call_tool(api_key_client, "plane_create_issue", {"project": "mcp test project", "name": "Minimal"})
    )
    assert not is_error
    assert payload["state"] == "Backlog"  # project default state
    assert payload["priority"] == "none"


@pytest.mark.contract
@pytest.mark.django_db
def test_update_issue(api_key_client, mcp_workspace, project, states, seeded_issue):
    payload, is_error = tool_payload(
        call_tool(
            api_key_client,
            "plane_update_issue",
            {
                "issue": "MCPX-1",
                "name": "Renamed by agent",
                "description_text": "Updated description",
                "priority": "low",
                "state": "Done",
                "assignees": ["me"],
            },
        )
    )
    assert not is_error, payload
    assert payload["name"] == "Renamed by agent"
    assert payload["state"] == "Done"
    assert payload["priority"] == "low"
    assert payload["description_text"] == "Updated description"

    seeded_issue.refresh_from_db()
    assert seeded_issue.priority == "low"
    assert seeded_issue.assignees.count() == 1
    # completed state syncs completed_at
    assert seeded_issue.completed_at is not None


@pytest.mark.contract
@pytest.mark.django_db
def test_add_comment(api_key_client, mcp_workspace, project, seeded_issue, workspace_member_user):
    payload, is_error = tool_payload(
        call_tool(api_key_client, "plane_add_comment", {"issue": "MCPX-1", "comment": "Agent was here"})
    )
    assert not is_error, payload
    comment = IssueComment.objects.get(issue=seeded_issue)
    assert comment.comment_stripped == "Agent was here"
    assert comment.actor == workspace_member_user


@pytest.mark.contract
@pytest.mark.django_db
def test_delete_issue(api_key_client, mcp_workspace, project, seeded_issue):
    payload, is_error = tool_payload(
        call_tool(api_key_client, "plane_delete_issue", {"issue": "MCPX-1"})
    )
    assert not is_error
    assert payload == {"deleted": True, "ref": "MCPX-1"}
    assert not Issue.objects.filter(id=seeded_issue.id).exists()


@pytest.mark.contract
@pytest.mark.django_db
def test_write_requires_project_member_role(api_key_client, mcp_workspace, project):
    outsider = User.objects.create(
        email="outsider@plane.so", username="outsider", first_name="Out", last_name="Sider"
    )
    WorkspaceMember.objects.create(workspace=mcp_workspace, member=outsider, role=15, is_active=True)
    # outsider has workspace membership but no ProjectMember row
    token = "outsider-token-12345"
    APIToken.objects.create(user=outsider, token=token, label="outsider")
    api_key_client.credentials(HTTP_X_API_KEY=token)

    payload, is_error = tool_payload(
        call_tool(api_key_client, "plane_create_issue", {"project": "MCPX", "name": "Sneaky"})
    )
    assert is_error
    assert "not found" in payload

    # reading by human ref is also project-membership gated
    Issue.objects.create(name="Secret", project=project, workspace=mcp_workspace)
    payload, is_error = tool_payload(
        call_tool(api_key_client, "plane_get_issue", {"issue": "MCPX-1"})
    )
    assert is_error
    assert "access" in payload

    # reads of unknown projects are also denied
    payload, is_error = tool_payload(
        call_tool(api_key_client, "plane_list_issues", {})
    )
    assert not is_error
    assert payload["total"] == 0


@pytest.mark.contract
@pytest.mark.django_db
def test_missing_required_param_is_tool_error(api_key_client):
    response = call_tool(api_key_client, "plane_create_issue", {"name": "No project"})
    payload, is_error = tool_payload(response)
    assert is_error
    assert "project" in payload


@pytest.mark.contract
@pytest.mark.django_db
def test_unknown_tool(api_key_client):
    payload, is_error = tool_payload(call_tool(api_key_client, "plane_not_a_tool", {}))
    assert is_error
    assert "Unknown tool" in payload
