# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
import hashlib
import hmac
import time
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlencode
from uuid import uuid4

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from plane.db.models import Issue, IssueAssignee, IssueComment, Label, Project, ProjectMember, State, User, Workspace
from plane.db.models.slack_delivery import SlackChannelMapping, SlackConnection, SlackEventDelivery
from plane.app.slack_delivery import commands, services
from plane.app.slack_delivery.client import SlackUnavailable, encrypt_token
from plane.app.slack_delivery.tasks import run_slack_command

pytestmark = [pytest.mark.contract, pytest.mark.django_db(transaction=True)]

CONFIG = {
    "CLIENT_ID": "123456.654321",
    "CLIENT_SECRET": "test-secret",
    "SIGNING_SECRET": "signing-test-secret",
    "BASE_URL": "http://localhost:3002",
}

RESPONSE_URL = "https://hooks.slack.com/actions/T0TEST1/API/xyz"


@pytest.fixture(autouse=True)
def boundaries(settings):
    settings.SLACK_DELIVERY = dict(CONFIG)
    with patch("plane.bgtasks.workitem_realtime._schedule_publish"), patch("celery.app.task.Task.apply_async"):
        yield


@pytest.fixture
def board(workspace, create_user):
    project = Project.objects.create(name="Commands", identifier="DEV", workspace=workspace)
    ProjectMember.objects.create(project=project, member=create_user, role=20, is_active=True)
    State.objects.create(name="Todo", group="unstarted", color="#555555", project=project, sequence=15000, default=True)
    State.objects.create(name="In Progress", group="started", color="#F59E0B", project=project, sequence=25000)
    State.objects.create(name="Done", group="completed", color="#46A758", project=project, sequence=35000)
    State.objects.create(name="Shipped", group="completed", color="#46A758", project=project, sequence=45000)
    issue = Issue.objects.create(name="Build feature", project=project)
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
        issue=issue,
        connection=connection,
        mapping=mapping,
        workspace=workspace,
        user=create_user,
    )


def actor_profile(email="test@plane.so"):
    return {"id": "U0ACTOR1", "profile": {"email": email}}


def slack_client_stub(profiles=None):
    """A SlackClient stand-in answering users.info for known Slack ids."""
    profiles = dict(profiles or {})
    stub = SimpleNamespace()
    stub.user_info = lambda token, user_id: profiles.get(user_id) or actor_profile()
    return stub


def signed_command(fields, secret=b"signing-test-secret"):
    raw = urlencode(fields).encode()
    stamp = str(int(time.time()))
    signature = "v0=" + hmac.new(secret, f"v0:{stamp}:".encode() + raw, hashlib.sha256).hexdigest()
    return raw, stamp, signature


def slash(client, text, *, command="/plane", stamp=None, signature=None, secret=b"signing-test-secret", extra=None):
    fields = {"command": command, "team_id": "T0TEST1", "channel_id": "C0CHANNEL", "user_id": "U0ACTOR1", "text": text}
    fields.update(extra or {})
    raw, default_stamp, default_signature = signed_command(fields, secret)
    return client.post(
        "/api/slack-delivery/commands/",
        data=raw,
        content_type="application/x-www-form-urlencoded",
        HTTP_X_SLACK_REQUEST_TIMESTAMP=stamp or default_stamp,
        HTTP_X_SLACK_SIGNATURE=signature or default_signature,
    )


def payload(board, text, response_url=RESPONSE_URL):
    return {
        "command": "/plane",
        "team_id": board.connection.team_id,
        "channel_id": board.mapping.channel_id,
        "user_id": "U0ACTOR1",
        "text": text,
        "response_url": response_url,
    }


def run(board, text):
    with patch.object(commands, "SlackClient", return_value=slack_client_stub()):
        return commands.execute(payload(board, text))


# --- endpoint contract ---


def test_command_retry_is_deduped(board, session_client):
    from plane.db.models.slack_delivery import SlackEventDelivery

    with patch("plane.app.slack_delivery.api.run_slack_command") as task:
        first = slash(session_client, "list")
        second = slash(session_client, "list")
    assert first.status_code == 200
    assert second.status_code == 200
    assert task.delay.call_count == 1
    assert SlackEventDelivery.objects.filter(event="command").count() == 1


def test_commands_signature_required(board, session_client):
    response = slash(session_client, "list")
    assert response.status_code == 200
    assert response.json() == {"response_type": "ephemeral", "text": "Working — the result will appear here shortly."}
    stale = str(int(time.time()) - 10_000)
    raw, _, _ = signed_command({"command": "/plane", "text": "help"})
    stale_sig = "v0=" + hmac.new(
        b"signing-test-secret", f"v0:{stale}:".encode() + raw, hashlib.sha256
    ).hexdigest()
    assert (
        session_client.post(
            "/api/slack-delivery/commands/",
            data=raw,
            content_type="application/x-www-form-urlencoded",
            HTTP_X_SLACK_REQUEST_TIMESTAMP=stale,
            HTTP_X_SLACK_SIGNATURE=stale_sig,
        ).status_code
        == 403
    )
    assert slash(session_client, "help", signature="v0=" + "0" * 64).status_code == 403


def test_commands_reject_wrong_command_and_ids(board, session_client):
    assert slash(session_client, "help", command="/other").status_code == 400
    assert slash(session_client, "help", extra={"team_id": "not-a-team!"}).status_code == 400
    assert slash(session_client, "help", extra={"channel_id": ""}).status_code == 400
    assert slash(session_client, "help", extra={"user_id": "x"}).status_code == 400


def test_commands_reject_oversized_body(board, session_client):
    assert slash(session_client, "help", extra={"text": "x" * 5000}).status_code == 400
    huge = {"command": "/plane", "text": "y" * 70000}
    raw, stamp, signature = signed_command(huge)
    response = session_client.post(
        "/api/slack-delivery/commands/",
        data=raw,
        content_type="application/x-www-form-urlencoded",
        HTTP_X_SLACK_REQUEST_TIMESTAMP=stamp,
        HTTP_X_SLACK_SIGNATURE=signature,
    )
    assert response.status_code == 413


@pytest.mark.parametrize("text", ["help", "", "   ", "frobnicate DEV-1"])
def test_help_is_answered_synchronously(board, session_client, text):
    response = slash(session_client, text)
    assert response.status_code == 200
    body = response.json()
    assert body["response_type"] == "ephemeral"
    for line in ("/plane create", "/plane view", "/plane assign", "/plane label", "/plane state", "/plane comment", "/plane close", "/plane list"):
        assert line in body["text"]


def test_command_enqueues_task_with_sanitized_response_url(board, session_client):
    with patch("plane.app.slack_delivery.api.run_slack_command") as task:
        response = slash(session_client, "list", extra={"response_url": "https://attacker.example.com/hook"})
    assert response.status_code == 200
    task.delay.assert_called_once_with(
        {
            "command": "/plane",
            "team_id": "T0TEST1",
            "channel_id": "C0CHANNEL",
            "user_id": "U0ACTOR1",
            "text": "list",
            "response_url": "",
            "trigger_id": "",
        }
    )


def test_task_posts_expected_errors_ephemerally(board):
    posted = {}

    def fake_post(url, body):
        posted["url"] = url
        posted["body"] = body
        return True

    with patch.object(commands, "SlackClient", return_value=slack_client_stub()), patch(
        "plane.app.slack_delivery.client.post_response_url", side_effect=fake_post
    ):
        run_slack_command.run(payload(board, "view DEV-9999"))
    assert posted["url"] == RESPONSE_URL
    assert posted["body"]["response_type"] == "ephemeral"
    assert "No work item DEV-9999 exists." in posted["body"]["text"]


def test_task_posts_success_in_channel(board):
    posted = {}
    with patch.object(commands, "SlackClient", return_value=slack_client_stub()), patch(
        "plane.app.slack_delivery.client.post_response_url",
        side_effect=lambda url, body: posted.update(url=url, body=body) or True,
    ):
        run_slack_command.run(payload(board, "list"))
    assert posted["body"]["response_type"] == "in_channel"
    assert f"{board.project.identifier}-{board.issue.sequence_id}" in posted["body"]["text"]


def test_post_response_url_host_validation():
    import plane.app.slack_delivery.client as client_module

    ok_response = SimpleNamespace(status_code=204)
    with patch.object(client_module.requests, "post", return_value=ok_response) as post:
        assert client_module.post_response_url("https://hooks.slack.com/x", {"text": "ok"})
        assert post.call_count == 1
        client_module.post_response_url("http://hooks.slack.com/x", {"text": "no"})
        client_module.post_response_url("https://attacker.example.com/hook", {"text": "no"})
        client_module.post_response_url("", {"text": "no"})
        client_module.post_response_url(None, {"text": "no"})
        assert post.call_count == 1
        post.side_effect = client_module.requests.RequestException("boom")
        assert client_module.post_response_url("https://hooks.slack.com/x", {"text": "ok"}) is False


# --- access control ---


def test_actor_must_be_workspace_member(board):
    with patch.object(commands, "SlackClient", return_value=slack_client_stub({"U0ACTOR1": {"profile": {}}})):
        with pytest.raises(commands.CommandError, match="no email"):
            commands.execute(payload(board, "list"))
    stranger = slack_client_stub({"U0ACTOR1": actor_profile("stranger@plane.so")})
    with patch.object(commands, "SlackClient", return_value=stranger):
        with pytest.raises(commands.CommandError, match="stranger@plane.so is not a member"):
            commands.execute(payload(board, "list"))


def test_actor_must_be_active_project_member(board):
    ProjectMember.objects.filter(project=board.project, member=board.user).update(role=5)
    with pytest.raises(commands.CommandError, match="not an active member"):
        run(board, f"view DEV-{board.issue.sequence_id}")
    ProjectMember.objects.filter(project=board.project, member=board.user).update(is_active=False)
    with pytest.raises(commands.CommandError, match="not an active member"):
        run(board, "list")
    ProjectMember.objects.filter(project=board.project, member=board.user).update(is_active=True, role=15)
    assert run(board, "list")["response_type"] == "in_channel"


# --- reference resolution ---


def test_ref_matrix(board):
    key = f"dev-{board.issue.sequence_id}"
    response = run(board, f"view {key}")
    assert "DEV-1 · Build feature" in response["text"]
    url = f"https://plane.example.com/{board.workspace.slug}/projects/{board.project.id}/issues/{board.issue.id}/"
    assert "Build feature" in run(board, f"view {url}")["text"]
    assert "Build feature" in run(board, f"view {board.issue.sequence_id}")["text"]
    with pytest.raises(commands.CommandError, match="not a work item reference"):
        run(board, "view DEV-1-2")
    with pytest.raises(commands.CommandError, match="No work item DEV-42 exists"):
        run(board, "view DEV-42")
    with pytest.raises(commands.CommandError, match="does not exist in this workspace"):
        run(board, f"view https://plane.example.com/x/projects/{board.project.id}/issues/{uuid4()}")
    with pytest.raises(commands.CommandError, match="No project with identifier NOPE"):
        run(board, "view NOPE-1")


def test_bare_number_needs_mapping(board):
    board.mapping.delete()
    with pytest.raises(commands.CommandError, match="mapped to a project"):
        run(board, f"view {board.issue.sequence_id}")
    assert "Build feature" in run(board, f"view DEV-{board.issue.sequence_id}")["text"]


def test_ref_cannot_cross_workspaces(board, create_user):
    other = Workspace.objects.create(name="Other", slug=f"other-{uuid4().hex[:6]}", owner=create_user)
    foreign_project = Project.objects.create(name="Foreign", identifier="FRN", workspace=other)
    foreign_issue = Issue.objects.create(name="Secret", project=foreign_project)
    with pytest.raises(commands.CommandError, match="does not exist in this workspace"):
        run(board, f"view https://any-host.example.com/w/projects/{foreign_project.id}/issues/{foreign_issue.id}")


# --- assign ---


def test_assign_by_mention_and_email(board, create_user):
    target = User.objects.create(email="target@plane.so", username="target@plane.so", first_name="Target")
    from plane.db.models import WorkspaceMember

    WorkspaceMember.objects.create(workspace=board.workspace, member=target, role=15, is_active=True)
    profiles = {"U0ACTOR1": actor_profile(), "U0TARGET": actor_profile("target@plane.so")}
    with patch.object(commands, "SlackClient", return_value=slack_client_stub(profiles)):
        response = commands.execute(payload(board, f"assign DEV-{board.issue.sequence_id} <@U0TARGET>"))
    assert response["response_type"] == "in_channel"
    assert "Assigned Target to DEV-1" in response["text"]
    assert "Assignees: Target" in response["text"]
    assert IssueAssignee.objects.filter(issue=board.issue, assignee=target).exists()
    # Repeating the assignment is idempotent and answers in channel.
    with patch.object(commands, "SlackClient", return_value=slack_client_stub(profiles)):
        repeat = commands.execute(payload(board, f"assign DEV-{board.issue.sequence_id} target@plane.so"))
    assert "Already assigned Target" in repeat["text"]
    assert IssueAssignee.objects.filter(issue=board.issue, assignee=target).count() == 1


def test_assign_unknown_target_never_leaks(board):
    profiles = {"U0ACTOR1": actor_profile(), "U0NOBODY": actor_profile("ghost@nowhere.so")}
    with patch.object(commands, "SlackClient", return_value=slack_client_stub(profiles)):
        with pytest.raises(commands.CommandError) as excinfo:
            commands.execute(payload(board, f"assign DEV-{board.issue.sequence_id} <@U0NOBODY>"))
    assert "not a member of this Plane workspace" in str(excinfo.value)
    # The looked-up address never echoes back to the channel.
    assert "ghost@nowhere.so" not in str(excinfo.value)
    with pytest.raises(commands.CommandError, match="No active member of this Plane workspace"):
        run(board, f"assign DEV-{board.issue.sequence_id} ghost@nowhere.so")
    with pytest.raises(commands.CommandError, match="No member of this Plane workspace"):
        run(board, f"assign DEV-{board.issue.sequence_id} Nobody Here")


def test_assign_by_name_requires_unique_match(board, create_user):
    jane = User.objects.create(email="jane@plane.so", username="jane@plane.so", first_name="Jane", last_name="Roe")
    second = User.objects.create(email="jane2@plane.so", username="jane2@plane.so", first_name="Jane", last_name="Doe")
    from plane.db.models import WorkspaceMember

    WorkspaceMember.objects.create(workspace=board.workspace, member=jane, role=15, is_active=True)
    WorkspaceMember.objects.create(workspace=board.workspace, member=second, role=15, is_active=True)
    assert "Assigned Jane Roe" in run(board, f"assign DEV-{board.issue.sequence_id} Jane Roe")["text"]
    with pytest.raises(commands.CommandError, match="Several members match"):
        run(board, f"assign DEV-{board.issue.sequence_id} Jane")


# --- label ---


def test_label_matches_and_creates(board):
    Label.objects.create(name="Bug", project=board.project, workspace=board.workspace)
    response = run(board, f"label DEV-{board.issue.sequence_id} bug, Urgent, bug")
    assert response["response_type"] == "in_channel"
    text = response["text"]
    assert "Labels: " in text and "Bug" in text and "Urgent" in text
    names = set(board.issue.labels.values_list("name", flat=True))
    assert names == {"Bug", "Urgent"}
    assert Label.objects.filter(project=board.project, name="Urgent", created_by=board.user).exists()
    with pytest.raises(commands.CommandError, match="at least one label"):
        run(board, f"label DEV-{board.issue.sequence_id} , ")


# --- state, close, comment ---


def test_state_case_insensitive(board):
    response = run(board, f"state DEV-{board.issue.sequence_id} in progress")
    assert "State: In Progress" in response["text"]
    board.issue.refresh_from_db()
    assert board.issue.state.name == "In Progress"
    with pytest.raises(commands.CommandError, match="No state named Missing"):
        run(board, f"state DEV-{board.issue.sequence_id} Missing")


def test_close_picks_default_completed_state(board):
    State.objects.filter(project=board.project, name="Shipped").update(default=True)
    response = run(board, f"close DEV-{board.issue.sequence_id}")
    board.issue.refresh_from_db()
    assert board.issue.state.name == "Shipped"
    assert board.issue.completed_at is not None
    assert "State: Shipped" in response["text"]


def test_close_falls_back_to_first_completed_state(board):
    run(board, f"close DEV-{board.issue.sequence_id}")
    board.issue.refresh_from_db()
    assert board.issue.state.name == "Done"


def test_comment_escapes_html(board):
    response = run(board, f"comment DEV-{board.issue.sequence_id} ships <b>now</b> & fast")
    comment = IssueComment.objects.get(issue=board.issue)
    assert comment.comment_html == "<p>ships &lt;b&gt;now&lt;/b&gt; &amp; fast</p>"
    assert comment.created_by == board.user
    assert "Commented on DEV-1" in response["text"]
    with pytest.raises(commands.CommandError, match="Comment with what"):
        run(board, f"comment DEV-{board.issue.sequence_id}  ")


# --- list ---


def test_list_caps_at_fifteen_and_hides_completed(board):
    for index in range(17):
        Issue.objects.create(name=f"Issue {index}", project=board.project)
    Issue.objects.create(name="Old work", project=board.project, state=State.objects.get(project=board.project, name="Shipped"))
    response = run(board, "list")
    assert response["response_type"] == "in_channel"
    lines = [line for line in response["text"].split("\n") if line.startswith("DEV-")]
    assert len(lines) == 15
    assert "Old work" not in response["text"]
    filtered = run(board, "list shipped")
    assert len([line for line in filtered["text"].split("\n") if line.startswith("DEV-")]) == 1
    assert "Old work" in filtered["text"]
    with pytest.raises(commands.CommandError, match="No state named"):
        run(board, "list nowhere")


# --- link unfurling ---


def unfurl_event(board, urls, ts="1727251210.000001", channel="C0CHANNEL"):
    return {
        "team_id": board.connection.team_id,
        "event_id": f"Ev{uuid4().hex[:10]}",
        "type": "event_callback",
        "event": {
            "type": "link_shared",
            "channel": channel,
            "message_ts": ts,
            "links": [{"url": url, "domain": "plane.example.com"} for url in urls],
        },
    }


def signed_event(payload, secret=b"signing-test-secret"):
    import json

    raw = json.dumps(payload).encode()
    stamp = str(int(time.time()))
    signature = "v0=" + hmac.new(secret, f"v0:{stamp}:".encode() + raw, hashlib.sha256).hexdigest()
    return raw, stamp, signature


def deliver_unfurl(session_client, payload):
    raw, stamp, signature = signed_event(payload)
    return session_client.post(
        "/api/slack-delivery/webhooks/",
        data=raw,
        content_type="application/json",
        HTTP_X_SLACK_REQUEST_TIMESTAMP=stamp,
        HTTP_X_SLACK_SIGNATURE=signature,
    )


def test_unfurl_posts_issue_summary(board, session_client):
    from plane.db.models import IssueLabel

    label = Label.objects.create(name="Bug", project=board.project, workspace=board.workspace)
    IssueLabel.objects.create(
        issue=board.issue, label=label, project=board.project, workspace=board.workspace
    )
    url = f"https://plane.example.com/{board.workspace.slug}/projects/{board.project.id}/issues/{board.issue.id}"
    payload_event = unfurl_event(board, [url, url + "/"])
    assert deliver_unfurl(session_client, payload_event).status_code == 202
    delivery = SlackEventDelivery.objects.get(id=payload_event["event_id"])
    assert delivery.status == "queued"
    with patch("plane.app.slack_delivery.unfurl.SlackClient") as client_class:
        # Work Object metadata first; the plain blocks card is the fallback.
        client_class.return_value.unfurl.side_effect = [SlackUnavailable("feature_not_enabled"), {"ok": True}]
        services.process_delivery(str(delivery.id))
    delivery.refresh_from_db()
    assert delivery.status == "processed"
    assert client_class.return_value.unfurl.call_count == 2
    metadata_call = client_class.return_value.unfurl.call_args_list[0]
    assert metadata_call.args[3] is None
    unfurl_entities = metadata_call.kwargs["metadata"]["entities"]
    assert len(unfurl_entities) == 2
    assert unfurl_entities[0]["entity_type"] == "slack#/entities/task"
    assert unfurl_entities[0]["external_ref"]["type"] == "plane_issue"
    assert unfurl_entities[0]["app_unfurl_url"] == url
    token, channel, ts, unfurls = client_class.return_value.unfurl.call_args_list[1].args
    assert token == "xoxb-test-token"
    assert channel == "C0CHANNEL" and ts == "1727251210.000001"
    assert set(unfurls) == {url, url + "/"}
    entry = unfurls[url]
    assert set(entry) == {"blocks", "fallback"}
    rendered = str(entry["blocks"])
    assert "DEV-1 · Build feature" in rendered
    board_url = f"http://localhost:3002/{board.workspace.slug}/projects/{board.project.id}/issues/{board.issue.id}"
    assert board_url in rendered
    assert "State: Todo" in rendered and "Assignees: Unassigned" in rendered and "Labels: Bug" in rendered
    # The card links to the board origin, never back to the shared host.
    assert "plane.example.com" not in rendered
    assert entry["fallback"].startswith("DEV-1 · Build feature\n")
    assert entry["fallback"].endswith(board_url)


def test_unfurl_skips_foreign_and_mismatched_links(board, session_client):
    foreign_workspace_url = f"https://evil.example.com/w/projects/{uuid4()}/issues/{uuid4()}"
    not_an_issue = "https://plane.example.com/some/other/path"
    payload_event = unfurl_event(board, [foreign_workspace_url, not_an_issue])
    assert deliver_unfurl(session_client, payload_event).status_code == 202
    delivery = SlackEventDelivery.objects.get(id=payload_event["event_id"])
    with patch("plane.app.slack_delivery.unfurl.SlackClient") as client_class:
        services.process_delivery(str(delivery.id))
    delivery.refresh_from_db()
    assert delivery.status == "processed"
    client_class.return_value.unfurl.assert_not_called()


def test_unfurl_ignored_without_connection(board, session_client):
    board.connection.is_active = False
    board.connection.save(update_fields=["is_active"])
    url = f"https://plane.example.com/{board.workspace.slug}/projects/{board.project.id}/issues/{board.issue.id}"
    payload_event = unfurl_event(board, [url])
    assert deliver_unfurl(session_client, payload_event).status_code == 202
    delivery = SlackEventDelivery.objects.get(id=payload_event["event_id"])
    assert delivery.status == "ignored"
    assert delivery.payload == {}


def test_get_requests_send_arguments_as_query_params(boundaries):
    """Slack ignores request bodies on GET; arguments must go in the query string."""
    from unittest.mock import patch

    from plane.app.slack_delivery.client import SlackClient

    with patch("plane.app.slack_delivery.client.requests.request") as call:
        call.return_value.status_code = 200
        call.return_value.json.return_value = {"ok": True, "user": {"name": "x"}}
        SlackClient()._request("GET", "/users.info", "xoxb-token", data={"user": "U123456"})
        kwargs = call.call_args.kwargs
        assert kwargs["params"] == {"user": "U123456"}
        assert kwargs["data"] is None
    with patch("plane.app.slack_delivery.client.requests.request") as call:
        call.return_value.status_code = 200
        call.return_value.json.return_value = {"ok": True}
        SlackClient()._request("POST", "/oauth.v2.access", data={"code": "c"})
        kwargs = call.call_args.kwargs
        assert kwargs["data"] == {"code": "c"}
        assert kwargs["params"] is None


# --- rich block builders ---


def test_slack_blocks_issue_escapes_and_defaults(board):
    from plane.app.slack_delivery.client import slack_blocks_issue

    board.issue.name = "Ship <@U999|ghost> `rm -rf` fast\nline two"
    board.issue.save(update_fields=["name"])
    blocks, fallback = slack_blocks_issue(board.issue, event_line="Assigned Jane to\nthis")
    assert len(blocks) == 3
    head = blocks[0]["text"]["text"]
    assert head.startswith(f"<http://localhost:3002/{board.workspace.slug}/")
    assert f"/projects/{board.project.id}/issues/{board.issue.id}|" in head
    assert "DEV-1 · Ship @U999ghost rm -rf fast line two>" in head
    # Only the link wrapper survives: escaped text carries no markup characters.
    assert head.count("<") == 1 and head.count(">") == 1 and head.count("|") == 1
    assert "`" not in str(blocks) and "\n" not in str(blocks)
    meta = blocks[1]["elements"][0]["text"]
    assert meta.startswith("State: Todo · Assignees: Unassigned")
    assert "Labels" not in meta  # labels are omitted when the issue has none
    assert blocks[2]["text"]["text"] == "Assigned Jane to this"
    assert fallback.startswith("DEV-1 · Ship @U999ghost rm -rf fast line two\n")
    assert fallback.endswith(
        f"http://localhost:3002/{board.workspace.slug}/projects/{board.project.id}/issues/{board.issue.id}"
    )


def test_slack_blocks_issue_lists_assignees_and_labels(board, create_user):
    from plane.app.slack_delivery.client import slack_blocks_issue
    from plane.db.models import IssueAssignee, IssueLabel

    assignee = User.objects.create(
        email="target@plane.so", username="target@plane.so", first_name="Target", last_name="One"
    )
    IssueAssignee.objects.create(
        issue=board.issue, assignee=assignee, project=board.project, workspace=board.workspace
    )
    label = Label.objects.create(name="Bug <b>", project=board.project, workspace=board.workspace)
    IssueLabel.objects.create(issue=board.issue, label=label, project=board.project, workspace=board.workspace)
    blocks, fallback = slack_blocks_issue(board.issue)
    meta = blocks[1]["elements"][0]["text"]
    assert meta == "State: Todo · Assignees: Target One · Labels: Bug b"
    assert "Bug b" in fallback and "Assignees: Target One" in fallback


def test_slack_blocks_base_url_falls_back_to_web_url(settings):
    import plane.app.slack_delivery.client as client_module

    settings.WEB_URL = "https://board.example.com"
    with patch.object(client_module, "configuration", return_value={"BASE_URL": ""}):
        assert client_module.slack_blocks_base_url() == "https://board.example.com"
    with patch.object(client_module, "configuration", return_value={"BASE_URL": "http://localhost:3002/"}):
        assert client_module.slack_blocks_base_url() == "http://localhost:3002"
    with patch.object(client_module, "configuration", return_value={"BASE_URL": ""}):
        settings.WEB_URL = ""
        assert client_module.slack_blocks_base_url() == ""


# --- block payloads on view and list ---


def test_view_and_list_carry_blocks(board):
    view = run(board, f"view DEV-{board.issue.sequence_id}")
    assert view["response_type"] == "in_channel"
    assert f"|DEV-{board.issue.sequence_id} · Build feature>" in view["blocks"][0]["text"]["text"]
    assert "State: Todo" in view["blocks"][1]["elements"][0]["text"]
    listing = run(board, "list")
    assert listing["response_type"] == "in_channel"
    assert listing["blocks"][0]["text"]["text"] == "DEV · open issues"
    assert any("DEV-1 Build feature" in block["text"]["text"] for block in listing["blocks"][1:])
    assert any(f"/projects/{board.project.id}/issues/{board.issue.id}|" in block["text"]["text"] for block in listing["blocks"][1:])


def test_list_empty_carries_blocks(board):
    Issue.objects.all().delete()
    listing = run(board, "list")
    assert listing["text"] == "DEV · open issues\nNo issues."
    assert listing["blocks"][0]["text"]["text"] == "DEV · open issues"
    assert listing["blocks"][1]["elements"][0]["text"] == "No issues."


# --- bare-KEY quick lookup ---


def test_bare_key_lookup_answers_ephemeral_view(board):
    response = run(board, f"DEV-{board.issue.sequence_id}")
    assert response["response_type"] == "ephemeral"
    assert f"Viewing DEV-{board.issue.sequence_id} · Build feature" in response["text"]
    assert f"|DEV-{board.issue.sequence_id} · Build feature>" in response["blocks"][0]["text"]["text"]


def test_bare_number_lookup(board):
    response = run(board, str(board.issue.sequence_id))
    assert response["response_type"] == "ephemeral"
    assert "Build feature" in response["text"]


def test_lookup_enqueues_task_like_other_actions(board, session_client):
    with patch("plane.app.slack_delivery.api.run_slack_command") as task:
        assert slash(session_client, "DEV-12").status_code == 200
    queued = task.delay.call_args[0][0]
    assert queued["text"] == "DEV-12"


def test_lookup_strictness_and_guards(board):
    # Multi-token input never parses as a bare reference: it answers help.
    assert run(board, f"DEV-{board.issue.sequence_id} extra") == {
        "response_type": "ephemeral",
        "text": commands.HELP_TEXT,
    }
    with pytest.raises(commands.CommandError, match="No work item DEV-42 exists"):
        run(board, "DEV-42")
    board.mapping.delete()
    with pytest.raises(commands.CommandError, match="mapped to a project"):
        run(board, str(board.issue.sequence_id))


def test_unknown_word_still_help_and_subcommands_win(board):
    assert run(board, "hlep") == {"response_type": "ephemeral", "text": commands.HELP_TEXT}
    # Subcommand words never fall through to lookup: `view` with no ref asks
    # for a reference instead of answering help.
    with pytest.raises(commands.CommandError, match="Which work item"):
        run(board, "view")


def test_help_lists_bare_lookup(board, session_client):
    response = slash(session_client, "help")
    assert response.status_code == 200
    assert "/plane KEY-12" in response.json()["text"]


def test_unfurl_keeps_same_workspace_guard_for_blocks(board, session_client):
    """Blocks never bypass the workspace scoping: foreign ids stay unfurled away."""
    foreign_workspace_url = f"https://plane.example.com/w/projects/{uuid4()}/issues/{uuid4()}"
    payload_event = unfurl_event(board, [foreign_workspace_url])
    assert deliver_unfurl(session_client, payload_event).status_code == 202
    delivery = SlackEventDelivery.objects.get(id=payload_event["event_id"])
    with patch("plane.app.slack_delivery.unfurl.SlackClient") as client_class:
        services.process_delivery(str(delivery.id))
    delivery.refresh_from_db()
    assert delivery.status == "processed"
    client_class.return_value.unfurl.assert_not_called()


def test_unfurl_browse_short_links(board, session_client):
    """Plane's /browse/KEY-12 short links unfurl like full issue URLs."""
    url = f"https://plane.example.com/{board.workspace.slug}/browse/{board.project.identifier}-{board.issue.sequence_id}"
    payload_event = unfurl_event(board, [url])
    assert deliver_unfurl(session_client, payload_event).status_code == 202
    delivery = SlackEventDelivery.objects.get(id=payload_event["event_id"])
    with patch("plane.app.slack_delivery.unfurl.SlackClient") as client_class:
        services.process_delivery(str(delivery.id))
    delivery.refresh_from_db()
    assert delivery.status == "processed"
    client_class.return_value.unfurl.assert_called_once()
    call = client_class.return_value.unfurl.call_args
    assert call.args[3] is None
    unfurl_entities = call.kwargs["metadata"]["entities"]
    assert len(unfurl_entities) == 1
    assert board.issue.project.identifier in unfurl_entities[0]["entity_payload"]["attributes"]["display_id"]


def test_unfurl_browse_link_of_other_workspace_is_skipped(board, session_client):
    url = "https://plane.example.com/other-workspace/browse/OTHER-1"
    payload_event = unfurl_event(board, [url])
    assert deliver_unfurl(session_client, payload_event).status_code == 202
    delivery = SlackEventDelivery.objects.get(id=payload_event["event_id"])
    with patch("plane.app.slack_delivery.unfurl.SlackClient") as client_class:
        services.process_delivery(str(delivery.id))
    client_class.return_value.unfurl.assert_not_called()


def test_view_command_accepts_browse_url(board, session_client):
    url = f"https://plane.example.com/{board.workspace.slug}/browse/{board.project.identifier}-{board.issue.sequence_id}"
    response = slash(session_client, f"view {url}")
    assert response.status_code == 200
