import hashlib
import hmac
import json
import time
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlencode

import pytest

from plane.app.release_intelligence.provider import IntelligenceError
from plane.app.slack_delivery.client import SlackClient, SlackUnavailable, app_manifest, encrypt_token
from plane.app.slack_delivery.tasks import run_slack_command
from plane.db.models import AIActionAudit, Issue, Project, State
from plane.db.models.slack_delivery import SlackChannelMapping, SlackConnection, SlackEventDelivery

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
    with patch("celery.app.task.Task.apply_async"), patch("plane.bgtasks.workitem_realtime._schedule_publish"):
        yield


@pytest.fixture
def board(workspace, create_user):
    project = Project.objects.create(name="Asks", identifier="ASK", workspace=workspace)
    state = State.objects.create(name="Todo", group="unstarted", color="#555555", project=project, default=True)
    connection = SlackConnection.objects.create(
        workspace=workspace,
        team_id="T0TEST1",
        team_name="Team",
        slack_user_id="U0AUTHOR",
        bot_user_id="U0BOT001",
        bot_token_encrypted=encrypt_token("xoxb-test-token"),
        connected_by=create_user,
    )
    mapping = SlackChannelMapping.objects.create(
        connection=connection, project=project, channel_id="C0CHANNEL", channel_name="general"
    )
    return SimpleNamespace(
        workspace=workspace, project=project, state=state, connection=connection, mapping=mapping, user=create_user
    )


def shortcut(callback="create_issue_from_thread", kind="message_shortcut"):
    return {
        "type": kind,
        "callback_id": callback,
        "team": {"id": "T0TEST1"},
        "user": {"id": "U0AUTHOR"},
        "channel": {"id": "C0CHANNEL"},
        "trigger_id": "123.456.token",
        "message": {"ts": "1727251210.000001", "text": "login broken"},
    }


def submit(board, title="Fix login", project=None):
    return {
        "type": "view_submission",
        "team": {"id": "T0TEST1"},
        "user": {"id": "U0AUTHOR"},
        "view": {
            "id": "V0TEST",
            "callback_id": "asks_create_issue",
            "private_metadata": json.dumps(
                {"team_id": "T0TEST1", "channel_id": "C0CHANNEL", "thread_ts": "1727251210.000001"}
            ),
            "state": {
                "values": {
                    "project": {"project": {"selected_option": {"value": str(project or board.project.id)}}},
                    "title": {"title": {"value": title}},
                    "description": {"description": {"value": "Step <one>\n\nNext & last"}},
                }
            },
        },
    }


def post(client, payload, path="interactivity"):
    raw = urlencode({"payload": json.dumps(payload)} if path == "interactivity" else payload).encode()
    stamp = str(int(time.time()))
    sig = "v0=" + hmac.new(b"signing-test-secret", f"v0:{stamp}:".encode() + raw, hashlib.sha256).hexdigest()
    return client.post(
        f"/api/slack-delivery/{path}/",
        data=raw,
        content_type="application/x-www-form-urlencoded",
        HTTP_X_SLACK_REQUEST_TIMESTAMP=stamp,
        HTTP_X_SLACK_SIGNATURE=sig,
    )


def question(text="login broken", command="/plane-ask"):
    return {
        "command": command,
        "team_id": "T0TEST1",
        "channel_id": "C0CHANNEL",
        "user_id": "U0AUTHOR",
        "text": text,
        "response_url": RESPONSE_URL,
    }


def test_unknown_shortcut_ignored(board, session_client):
    with patch.object(SlackClient, "_request") as call:
        assert post(session_client, shortcut("unknown")).status_code == 202
    call.assert_not_called()
    assert SlackEventDelivery.objects.filter(status="ignored", event="message_shortcut").exists()


@pytest.mark.parametrize("kind", ["message_shortcut", "message_action"])
def test_draft_modal(board, session_client, kind):
    with (
        patch(
            "plane.app.release_intelligence.provider.generate_text",
            return_value={"text": "# Fix the login bug\n\n- step one\n- step two", "model": "openai/test-model"},
        ) as generate,
        patch.object(
            SlackClient,
            "conversations_replies",
            return_value=[{"user": "U0AUTHOR", "text": "login broken", "ts": "1727251210.000001"}],
        ),
        patch.object(SlackClient, "views_open") as opened,
    ):
        assert post(session_client, shortcut(kind=kind)).status_code == 200
    modal = opened.call_args.args[2]
    assert modal["callback_id"] == "asks_create_issue"
    assert json.loads(modal["private_metadata"])["channel_id"] == "C0CHANNEL"
    blocks = {b["block_id"]: b for b in modal["blocks"]}
    assert blocks["title"]["element"]["initial_value"] == "Fix the login bug"
    assert blocks["description"]["element"]["initial_value"] == "- step one\n- step two"
    assert blocks["project"]["element"]["options"][0]["value"] == str(board.project.id)
    assert generate.call_args.kwargs["project"] == board.project
    assert isinstance(generate.call_args.kwargs["sources"], list)
    assert AIActionAudit.objects.filter(action="slack.asks_draft", model="openai/test-model").exists()


def test_draft_ai_error(board, session_client):
    with (
        patch("plane.app.release_intelligence.provider.generate_text", side_effect=IntelligenceError("missing key")),
        patch.object(SlackClient, "conversations_replies", return_value=[{"text": "login broken"}]),
        patch.object(SlackClient, "views_open") as opened,
        patch.object(SlackClient, "post_ephemeral") as ephemeral,
    ):
        assert post(session_client, shortcut()).status_code == 200
    opened.assert_not_called()
    assert "configure" in ephemeral.call_args.args[3].lower()
    assert AIActionAudit.objects.filter(action="slack.asks_draft", status=AIActionAudit.Status.ERROR).exists()


def test_create_submission_and_dedupe(board, session_client):
    with (
        patch.object(SlackClient, "user_info", return_value={"profile": {"email": board.user.email}}),
        patch.object(SlackClient, "post_message") as message,
    ):
        response = post(session_client, submit(board))
        assert response.json() == {"response_action": "clear"}
        assert post(session_client, submit(board)).json() == {"response_action": "clear"}
    issue = Issue.objects.get(name="Fix login")
    assert issue.project == board.project and issue.created_by == board.user and issue.state == board.state
    assert issue.description_html == "<p>Step &lt;one&gt;</p><p>Next &amp; last</p>"
    assert str(issue.id) in message.call_args.args[3]
    assert f"ASK-{issue.sequence_id}" in message.call_args.args[3]
    message.assert_called_once()
    assert AIActionAudit.objects.filter(action="slack.asks_create", entity_type="issue", entity_id=issue.id).exists()


@pytest.mark.parametrize("title,project", [("", None), ("Title", "invalid-project")])
def test_submission_validation(board, session_client, title, project):
    response = post(session_client, submit(board, title=title, project=project))
    assert response.status_code == 200 and response.json()["response_action"] == "errors"
    assert not Issue.objects.filter(name="Title").exists()


def test_create_slack_failure_keeps_issue(board, session_client):
    with (
        patch.object(SlackClient, "user_info", side_effect=SlackUnavailable()),
        patch.object(SlackClient, "post_message", side_effect=SlackUnavailable("missing_scope")),
    ):
        assert post(session_client, submit(board)).json() == {"response_action": "clear"}
    assert Issue.objects.get(name="Fix login").created_by == board.user
    assert SlackEventDelivery.objects.filter(status="ignored").exists()
    assert AIActionAudit.objects.filter(action="slack.asks_create", status=AIActionAudit.Status.ERROR).exists()


def test_ask_endpoint_queues(board, session_client):
    with patch("plane.app.slack_delivery.api.run_slack_command") as task:
        assert post(session_client, question(), path="commands").status_code == 200
        assert task.delay.call_args.args[0]["command"] == "/plane-ask"
        assert post(session_client, question("ask login", "/plane"), path="commands").status_code == 200
        assert task.delay.call_count == 2


@pytest.mark.parametrize("error", [False, True])
def test_ask_answer_and_error(board, error):
    issue = Issue.objects.create(project=board.project, name="Login broken", description_html="<p>Failure</p>")
    with (
        patch.object(SlackClient, "user_info", return_value={"profile": {"email": board.user.email}}),
        patch(
            "plane.app.release_intelligence.provider.generate_text",
            **(
                {"side_effect": IntelligenceError("missing key")}
                if error
                else {"return_value": {"text": "The login needs repair.", "model": "openai/test-model"}}
            ),
        ) as generate,
        patch("plane.app.slack_delivery.client.post_response_url") as response,
    ):
        run_slack_command(question())
    assert response.call_args.args[0] == RESPONSE_URL
    answer = response.call_args.args[1]
    assert answer["response_type"] == "ephemeral"
    if error:
        assert "configure" in answer["text"].lower()
    else:
        assert "The login needs repair." in answer["text"] and "Sources:" in answer["text"]
        assert str(issue.id) in answer["text"]
    assert generate.call_count == 1
    assert AIActionAudit.objects.filter(
        action="slack.ask", status=AIActionAudit.Status.ERROR if error else AIActionAudit.Status.SUCCESS
    ).exists()


def test_ask_matches_work_item_key(board):
    from plane.db.models import Issue

    hidden = Issue.objects.create(
        name="Totally different words here", project=board.project, workspace=board.workspace
    )
    with (
        patch(
            "plane.app.release_intelligence.provider.generate_text",
            return_value={"text": "Here is the summary.", "model": "test-model"},
        ) as generate,
        patch("plane.app.slack_delivery.client.post_response_url") as response,
    ):
        run_slack_command(question(f"what is ASK-{hidden.sequence_id} about?"))
    generate.assert_called_once()
    assert str(hidden.id) in response.call_args.args[1]["text"]


def test_ask_no_matches(board):
    with (
        patch("plane.app.release_intelligence.provider.generate_text") as generate,
        patch("plane.app.slack_delivery.client.post_response_url") as response,
    ):
        run_slack_command(question())
    generate.assert_not_called()
    assert "no matches" in response.call_args.args[1]["text"].lower()


def test_manifest():
    features = app_manifest("https://plane.example.com")["features"]
    assert features["shortcuts"][0]["callback_id"] == "create_issue_from_thread"
    assert "/plane-ask" in [c["command"] for c in features["slash_commands"]]


def test_ask_searches_comments_and_hides_archived(board):
    from plane.db.models import IssueComment
    from django.utils import timezone

    issue = Issue.objects.create(project=board.project, name="Unrelated title")
    IssueComment.objects.create(
        issue=issue, project=board.project, workspace=board.workspace, comment_html="<p>Authentication crashes</p>"
    )
    Issue.objects.create(project=board.project, name="Authentication archived", archived_at=timezone.now().date())
    with (
        patch(
            "plane.app.release_intelligence.provider.generate_text", return_value={"text": "Answer", "model": "test"}
        ) as generate,
        patch("plane.app.slack_delivery.client.post_response_url") as response,
    ):
        run_slack_command(question("authentication"))
    assert len(generate.call_args.kwargs["sources"]) == 1
    assert "Authentication crashes" in generate.call_args.kwargs["sources"][0]["content"]
    assert str(issue.id) in response.call_args.args[1]["text"]


def test_ask_without_project_context_lists_matches(board):
    Issue.objects.create(project=board.project, name="Login broken")
    board.mapping.delete()
    with (
        patch("plane.app.release_intelligence.provider.generate_text") as generate,
        patch("plane.app.slack_delivery.client.post_response_url") as response,
    ):
        run_slack_command(question())
    generate.assert_not_called()
    assert "Sources:" in response.call_args.args[1]["text"]


def test_ask_limits_answer(board):
    for index in range(9):
        Issue.objects.create(project=board.project, name=f"Login broken {index}")
    with (
        patch(
            "plane.app.release_intelligence.provider.generate_text", return_value={"text": "a" * 5000, "model": "test"}
        ) as generate,
        patch("plane.app.slack_delivery.client.post_response_url") as response,
    ):
        run_slack_command(question())
    assert len(generate.call_args.kwargs["sources"]) == 8
    assert len(response.call_args.args[1]["text"]) <= 2900
    assert "Sources:" in response.call_args.args[1]["text"]


def test_submission_rejects_unmapped_project(board, session_client):
    other = Project.objects.create(name="Unmapped", identifier="OTH", workspace=board.workspace)
    assert post(session_client, submit(board, project=other.id)).json()["response_action"] == "errors"
    assert not Issue.objects.exists()


def test_shortcut_retry_deduplicates(board, session_client):
    with (
        patch(
            "plane.app.release_intelligence.provider.generate_text",
            return_value={"text": "Title\nDescription", "model": "test"},
        ) as generate,
        patch.object(SlackClient, "conversations_replies", return_value=[{"text": "Content"}]),
        patch.object(SlackClient, "views_open") as opened,
    ):
        assert post(session_client, shortcut()).status_code == 200
        assert post(session_client, shortcut()).status_code == 200
    generate.assert_called_once()
    opened.assert_called_once()


def test_slack_client_methods(boundaries):
    with patch.object(SlackClient, "_request", return_value={"messages": [{"text": "thread"}]}) as call:
        client = SlackClient()
        assert client.conversations_replies("token", "channel", "ts") == [{"text": "thread"}]
        assert call.call_args.args[:2] == ("GET", "/conversations.replies")
        client.views_open("token", "trigger", {"type": "modal"})
        assert json.loads(call.call_args.kwargs["data"]["view"]) == {"type": "modal"}
        client.post_ephemeral("token", "channel", "user", "hint")
        assert call.call_args.args[:2] == ("POST", "/chat.postEphemeral")
        client.post_message("token", "channel", [], "created", thread_ts="123")
        assert call.call_args.kwargs["data"]["thread_ts"] == "123"


def test_manifest_requests_message_scope():
    assert "chat:write" in app_manifest("https://plane.example.com")["oauth_config"]["scopes"]["bot"]


def test_malformed_unknown_view_is_ignored(board, session_client):
    assert post(session_client, {"type": "view_submission", "view": "invalid"}).status_code == 202
