# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
"""Contract tests for the `/plane create` dialog (Asana-style modal clone)."""
import hashlib
import hmac
import json
import time
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlencode

import pytest
from rest_framework.test import APIClient

from plane.app.slack_delivery.client import encrypt_token
from plane.db.models import Issue, IssueAssignee, IssueSubscriber, Project, ProjectMember, State, User, WorkspaceMember
from plane.db.models.slack_delivery import SlackChannelMapping, SlackConnection, SlackEventDelivery, SlackIssueLink, SlackMessage

pytestmark = [pytest.mark.contract, pytest.mark.django_db(transaction=True)]

CONFIG = {
    "CLIENT_ID": "123456.654321",
    "CLIENT_SECRET": "test-secret",
    "SIGNING_SECRET": "signing-test-secret",
    "BASE_URL": "http://localhost:3002",
}
CALLBACK_ID = "plane_create_issue"


@pytest.fixture(autouse=True)
def boundaries(settings):
    settings.SLACK_DELIVERY = dict(CONFIG)
    with patch("celery.app.task.Task.apply_async"), patch("plane.bgtasks.workitem_realtime._schedule_publish"):
        yield


@pytest.fixture
def board(workspace, create_user):
    project = Project.objects.create(name="Create", identifier="DEV", workspace=workspace)
    ProjectMember.objects.create(project=project, member=create_user, role=20, is_active=True)
    State.objects.create(name="Todo", group="unstarted", color="#555555", project=project, sequence=15000, default=True)
    State.objects.create(name="Done", group="completed", color="#46A758", project=project, sequence=35000)
    beta = Project.objects.create(name="Beta", identifier="BET", workspace=workspace)
    ProjectMember.objects.create(project=beta, member=create_user, role=15, is_active=True)
    buddy = User.objects.create(email="buddy@plane.so", username="buddy@plane.so", first_name="Buddy")
    WorkspaceMember.objects.create(workspace=workspace, member=buddy, role=15, is_active=True)
    pal = User.objects.create(email="pal@plane.so", username="pal@plane.so", first_name="Pal")
    WorkspaceMember.objects.create(workspace=workspace, member=pal, role=15, is_active=True)
    connection = SlackConnection.objects.create(
        workspace=workspace,
        team_id="T0TEST1",
        team_name="Team",
        team_domain="team",
        slack_user_id="U0AUTHOR",
        bot_user_id="U0BOT001",
        bot_token_encrypted=encrypt_token("xoxb-test-token"),
        connected_by=create_user,
    )
    mapping = SlackChannelMapping.objects.create(
        connection=connection, project=project, channel_id="C0CHANNEL", channel_name="general"
    )
    return SimpleNamespace(
        project=project,
        beta=beta,
        buddy=buddy,
        pal=pal,
        connection=connection,
        mapping=mapping,
        workspace=workspace,
        user=create_user,
    )


def actor_profile(email="test@plane.so"):
    return {"id": "U0ACTOR1", "profile": {"email": email}}


def profiles_stub(extra=None):
    known = {"U0ACTOR1": actor_profile(), "U0BUDDY": actor_profile("buddy@plane.so"), "U0PAL": actor_profile("pal@plane.so")}
    known.update(extra or {})
    return known


def slack_stub(profiles=None, **methods):
    known = profiles_stub(profiles)
    stub = SimpleNamespace()
    stub.user_info = lambda token, user_id: known.get(user_id) or {"id": user_id, "profile": {}}
    if "post_message" not in methods:
        # Harmless default so submission paths that post a card never 500.
        methods["post_message"] = lambda token, channel, blocks, text, **kwargs: {
            "channel": channel, "ts": "1727251300.000900", "message": {"text": text}
        }
    if "post_ephemeral" not in methods:
        methods["post_ephemeral"] = lambda token, channel, user, text, **kwargs: {"ok": True}
    for name, fn in methods.items():
        setattr(stub, name, fn)
    return stub


def signed_command(fields, secret=b"signing-test-secret"):
    raw = urlencode(fields).encode()
    stamp = str(int(time.time()))
    signature = "v0=" + hmac.new(secret, f"v0:{stamp}:".encode() + raw, hashlib.sha256).hexdigest()
    return raw, stamp, signature


def slash(client, text, *, extra=None):
    fields = {"command": "/plane", "team_id": "T0TEST1", "channel_id": "C0CHANNEL", "user_id": "U0ACTOR1", "text": text}
    fields.update(extra or {})
    raw, stamp, signature = signed_command(fields)
    return client.post(
        "/api/slack-delivery/commands/",
        data=raw,
        content_type="application/x-www-form-urlencoded",
        HTTP_X_SLACK_REQUEST_TIMESTAMP=stamp,
        HTTP_X_SLACK_SIGNATURE=signature,
    )


def submit_payload(board, *, title="Fix login", project=None, assignee=None, due=None, description="", channel="C0CHANNEL", collaborators=None, view_id="V0MODAL"):
    values = {
        "title": {"title": {"value": title}},
        "project": {"project": {"selected_option": {"value": str(project or board.project.id)}}},
    }
    if assignee:
        values["assignee"] = {"assignee": {"selected_user": assignee}}
    if due:
        values["due"] = {"due": {"selected_date": due}}
    if description:
        values["description"] = {"description": {"value": description}}
    if channel:
        values["channel"] = {"channel": {"selected_conversation": channel}}
    if collaborators:
        values["collaborators"] = {"collaborators": {"selected_users": collaborators}}
    return {
        "type": "view_submission",
        "team": {"id": "T0TEST1"},
        "user": {"id": "U0ACTOR1"},
        "view": {
            "id": view_id,
            "callback_id": CALLBACK_ID,
            "private_metadata": json.dumps({"team_id": "T0TEST1", "channel_id": "C0CHANNEL", "user_id": "U0ACTOR1"}),
            "state": {"values": values},
        },
    }


def post_interaction(client, payload):
    raw = urlencode({"payload": json.dumps(payload)}).encode()
    stamp = str(int(time.time()))
    signature = "v0=" + hmac.new(b"signing-test-secret", f"v0:{stamp}:".encode() + raw, hashlib.sha256).hexdigest()
    return client.post(
        "/api/slack-delivery/interactivity/",
        data=raw,
        content_type="application/x-www-form-urlencoded",
        HTTP_X_SLACK_REQUEST_TIMESTAMP=stamp,
        HTTP_X_SLACK_SIGNATURE=signature,
    )


# --- modal open ---


def test_create_opens_modal_inline(board, session_client):
    opened = []

    def views_open(token, trigger_id, view):
        assert trigger_id == "TRIG.123.token"
        opened.append(view)

    with patch("plane.app.slack_delivery.commands.SlackClient", return_value=slack_stub()):
        with patch("plane.app.slack_delivery.create_modal.SlackClient", return_value=slack_stub(views_open=views_open)):
            response = slash(session_client, "create Fix the login flow", extra={"trigger_id": "TRIG.123.token"})
    assert response.status_code == 200
    assert response.content == b""
    assert len(opened) == 1
    view = opened[0]
    assert view["callback_id"] == CALLBACK_ID
    assert view["title"]["text"] == "New Plane task"
    assert view["submit"]["text"] == "Create"
    blocks = {block.get("block_id"): block for block in view["blocks"] if block.get("type") == "input"}
    assert set(blocks) == {"title", "assignee", "project", "due", "description", "channel", "collaborators"}
    assert blocks["title"]["element"]["initial_value"] == "Fix the login flow"
    assert blocks["project"]["element"]["initial_option"]["value"] == str(board.project.id)
    assert blocks["channel"]["element"]["initial_conversation"] == "C0CHANNEL"
    assert json.loads(view["private_metadata"])["team_id"] == "T0TEST1"
    assert SlackEventDelivery.objects.filter(event="command", status="processed").exists()


def test_create_retry_is_deduped(board, session_client):
    opened = []
    views_open = lambda token, trigger_id, view: opened.append(view)  # noqa: E731
    with patch("plane.app.slack_delivery.commands.SlackClient", return_value=slack_stub()):
        with patch("plane.app.slack_delivery.create_modal.SlackClient", return_value=slack_stub(views_open=views_open)):
            assert slash(session_client, "create Same body", extra={"trigger_id": "T1"}).status_code == 200
            assert slash(session_client, "create Same body", extra={"trigger_id": "T1"}).status_code == 200
    assert len(opened) == 1


def test_create_without_trigger_id_frees_dedupe_slot(board, session_client):
    opened = []
    views_open = lambda token, trigger_id, view: opened.append(view)  # noqa: E731
    with patch("plane.app.slack_delivery.commands.SlackClient", return_value=slack_stub()):
        with patch("plane.app.slack_delivery.create_modal.SlackClient", return_value=slack_stub(views_open=views_open)):
            first = slash(session_client, "create Fix the login flow")
            assert first.status_code == 200
            assert first.json()["response_type"] == "ephemeral"
            second = slash(session_client, "create Fix the login flow", extra={"trigger_id": "T1"})
            assert second.status_code == 200
    assert len(opened) == 1
    assert SlackEventDelivery.objects.filter(event="command").count() == 1


def test_create_key_preselects_project(board, session_client):
    opened = []

    def views_open(token, trigger_id, view):
        opened.append(view)

    with patch("plane.app.slack_delivery.commands.SlackClient", return_value=slack_stub()):
        with patch("plane.app.slack_delivery.create_modal.SlackClient", return_value=slack_stub(views_open=views_open)):
            slash(session_client, "create BET Trim the backlog", extra={"trigger_id": "T1"})
    blocks = {block.get("block_id"): block for block in opened[0]["blocks"] if block.get("type") == "input"}
    assert blocks["title"]["element"]["initial_value"] == "Trim the backlog"
    assert blocks["project"]["element"]["initial_option"]["value"] == str(board.beta.id)


def test_create_defaults_to_mapped_project(board, session_client):
    opened = []

    def views_open(token, trigger_id, view):
        opened.append(view)

    with patch("plane.app.slack_delivery.commands.SlackClient", return_value=slack_stub()):
        with patch("plane.app.slack_delivery.create_modal.SlackClient", return_value=slack_stub(views_open=views_open)):
            slash(session_client, "create", extra={"trigger_id": "T1"})
    blocks = {block.get("block_id"): block for block in opened[0]["blocks"] if block.get("type") == "input"}
    assert blocks["project"]["element"]["initial_option"]["value"] == str(board.project.id)
    assert "initial_value" not in blocks["title"]["element"]


def test_create_without_project_membership_is_ephemeral_error(board, session_client):
    ProjectMember.objects.all().delete()
    opened = []
    views_open = lambda token, trigger_id, view: opened.append(view)  # noqa: E731
    with patch("plane.app.slack_delivery.commands.SlackClient", return_value=slack_stub()):
        with patch("plane.app.slack_delivery.create_modal.SlackClient", return_value=slack_stub(views_open=views_open)):
            response = slash(session_client, "create Fix the login flow", extra={"trigger_id": "T1"})
    assert response.json()["response_type"] == "ephemeral"
    assert "project" in response.json()["text"]
    assert opened == []


# --- submission ---


def test_submission_happy_path(board, session_client):
    posted = []

    def post_message(token, channel, blocks, text, **kwargs):
        posted.append((channel, blocks, text))
        return {"channel": channel, "ts": "1727251300.000100", "message": {"text": text}}

    payload = submit_payload(
        board,
        title="Fix the login flow",
        assignee="U0BUDDY",
        due="2026-10-31",
        description="Steps:\n1. open app\n2. sign in",
        channel="C0CHANNEL",
        collaborators=["U0PAL"],
        view_id="V0HAPPY",
    )
    with patch("plane.app.slack_delivery.commands.SlackClient", return_value=slack_stub()):
        with patch("plane.app.slack_delivery.create_modal.SlackClient", return_value=slack_stub(post_message=post_message)):
            with patch("plane.app.slack_delivery.notify._schedule") as schedule:
                response = post_interaction(session_client, payload)
    assert response.status_code == 200, getattr(response, "data", response.content)
    assert response.json() == {"response_action": "clear"}
    assert not schedule.called
    issue = Issue.objects.get(project=board.project, name="Fix the login flow")
    assert issue.created_by == board.user
    assert issue.state.name == "Todo"
    assert issue.target_date == date(2026, 10, 31)
    assert IssueAssignee.objects.filter(issue=issue, assignee=board.buddy).exists()
    assert IssueSubscriber.objects.filter(issue=issue, subscriber=board.pal).exists()
    assert len(posted) == 1
    channel, blocks, _text = posted[0]
    assert channel == "C0CHANNEL"
    assert "created a new work item" in blocks[0]["text"]["text"]
    actions = blocks[-1]["elements"]
    assert actions[0]["text"]["text"] == "Open in Plane"
    assert actions[1]["action_id"] == "plane:assign-me"
    assert actions[1]["value"] == str(issue.id)
    # The card is recorded so it shows on the work item's Slack panel.
    message = SlackMessage.objects.get(mapping=board.mapping, ts="1727251300.000100")
    assert SlackIssueLink.objects.filter(message=message, issue=issue).exists()
    assert SlackEventDelivery.objects.filter(event="view_submission", status="processed").exists()


def test_submission_without_channel_confirms_ephemerally(board, session_client):
    ephemerals = []
    posted = []

    def post_ephemeral(token, channel, user, text, **kwargs):
        ephemerals.append((channel, user, text))

    def post_message(token, channel, blocks, text, **kwargs):
        posted.append((channel, blocks, text))
        return {"channel": channel, "ts": "1727251300.000200", "message": {"text": text}}

    payload = submit_payload(board, title="Quiet ticket", channel=None, view_id="V0QUIET")
    with patch("plane.app.slack_delivery.commands.SlackClient", return_value=slack_stub()):
        with patch(
            "plane.app.slack_delivery.create_modal.SlackClient",
            return_value=slack_stub(post_message=post_message, post_ephemeral=post_ephemeral),
        ):
            response = post_interaction(session_client, payload)
    assert response.json() == {"response_action": "clear"}
    assert posted == []
    assert len(ephemerals) == 1
    assert ephemerals[0][0] == "C0CHANNEL"
    issue = Issue.objects.get(name="Quiet ticket")
    assert issue.target_date is None
    assert not IssueAssignee.objects.filter(issue=issue).exists()


def test_submission_to_unmapped_channel_skips_link(board, session_client):
    posted = []

    def post_message(token, channel, blocks, text, **kwargs):
        posted.append(channel)
        return {"channel": channel, "ts": "1727251300.000300", "message": {"text": text}}

    payload = submit_payload(board, title="Elsewhere", channel="C0OTHER", view_id="V0OTHER")
    with patch("plane.app.slack_delivery.commands.SlackClient", return_value=slack_stub()):
        with patch("plane.app.slack_delivery.create_modal.SlackClient", return_value=slack_stub(post_message=post_message)):
            response = post_interaction(session_client, payload)
    assert response.json() == {"response_action": "clear"}
    assert posted == ["C0OTHER"]
    assert not SlackMessage.objects.filter(ts="1727251300.000300").exists()


def test_submission_validation_errors(board, session_client):
    payload = submit_payload(board, title="ab", view_id="V0BAD1")
    with patch("plane.app.slack_delivery.commands.SlackClient", return_value=slack_stub()):
        with patch("plane.app.slack_delivery.create_modal.SlackClient", return_value=slack_stub()):
            response = post_interaction(session_client, payload)
    assert response.json()["response_action"] == "errors"
    assert "title" in response.json()["errors"]
    assert not Issue.objects.filter(project=board.project).exclude(name="Build feature").exists()

    # A due date Slack never sends is still validated defensively.
    payload = submit_payload(board, title="Valid title", due="not-a-date", view_id="V0BAD2")
    with patch("plane.app.slack_delivery.commands.SlackClient", return_value=slack_stub()):
        with patch("plane.app.slack_delivery.create_modal.SlackClient", return_value=slack_stub()):
            response = post_interaction(session_client, payload)
    assert response.json()["errors"] == {"due": "Enter a valid date."}


def test_submission_rejects_outside_assignee_and_project(board, session_client):
    foreign = Project.objects.create(name="Foreign", identifier="FRN", workspace=board.workspace)
    payload = submit_payload(board, title="Valid title", project=foreign.id, view_id="V0BAD3")
    with patch("plane.app.slack_delivery.commands.SlackClient", return_value=slack_stub()):
        with patch("plane.app.slack_delivery.create_modal.SlackClient", return_value=slack_stub()):
            response = post_interaction(session_client, payload)
    assert "project" in response.json()["errors"]

    payload = submit_payload(board, title="Valid title", assignee="U0STRANGER", view_id="V0BAD4")
    with patch("plane.app.slack_delivery.commands.SlackClient", return_value=slack_stub()):
        with patch("plane.app.slack_delivery.create_modal.SlackClient", return_value=slack_stub()):
            response = post_interaction(session_client, payload)
    assert "assignee" in response.json()["errors"]
    assert not Issue.objects.filter(name="Valid title").exists()


def test_submission_retry_after_claim_is_clear(board, session_client):
    payload = submit_payload(board, title="Once only", view_id="V0SAME")
    with patch("plane.app.slack_delivery.commands.SlackClient", return_value=slack_stub()):
        with patch("plane.app.slack_delivery.create_modal.SlackClient", return_value=slack_stub()):
            first = post_interaction(session_client, payload)
            second = post_interaction(session_client, payload)
    assert first.json() == {"response_action": "clear"}
    assert second.json() == {"response_action": "clear"}
    assert Issue.objects.filter(name="Once only").count() == 1


def test_submission_for_stranger_workspace_ignored(board, session_client):
    payload = submit_payload(board, view_id="V0NOCONN")
    payload["team"] = {"id": "T0OTHER"}
    with patch("plane.app.slack_delivery.commands.SlackClient", return_value=slack_stub()):
        with patch("plane.app.slack_delivery.create_modal.SlackClient", return_value=slack_stub()):
            response = post_interaction(session_client, payload)
    assert response.json() == {}
    assert not Issue.objects.exists()


def test_unknown_view_submission_ignored(board, session_client):
    payload = submit_payload(board, view_id="V0UNKNOWN")
    payload["view"]["callback_id"] = "some_other_dialog"
    with patch("plane.app.slack_delivery.commands.SlackClient", return_value=slack_stub()):
        with patch("plane.app.slack_delivery.create_modal.SlackClient", return_value=slack_stub()):
            response = post_interaction(session_client, payload)
    assert response.status_code == 202
    assert not Issue.objects.exists()
