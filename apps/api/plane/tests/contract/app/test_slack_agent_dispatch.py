# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
"""Contract tests for the agent-dispatch connector (SPECS/agent-dispatch-connector.md).

All dispatcher traffic goes through a FakeDispatcher (no real network) and all
Slack traffic through FakeSlack; every email in the fixture data is @example.com.
"""
import hashlib
import hmac
import json
import re
import time
from contextlib import ExitStack
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlencode, urlsplit
from uuid import uuid4

import pytest
from rest_framework.test import APIClient

from plane.db.models import (
    Issue,
    IssueComment,
    Project,
    ProjectMember,
    State,
    User,
    Workspace,
    WorkspaceMember,
)
from plane.db.models.slack_delivery import SlackAgentJob, SlackChannelMapping, SlackConnection, SlackEventDelivery
from plane.app.slack_delivery import agent_dispatch, commands, entities, services
from plane.app.slack_delivery.client import SlackClient, encrypt_token
from plane.app.slack_delivery.tasks import run_slack_command, run_slack_interactivity

pytestmark = [pytest.mark.contract, pytest.mark.django_db(transaction=True)]

CONFIG = {
    "CLIENT_ID": "123456.654321",
    "CLIENT_SECRET": "test-secret",
    "SIGNING_SECRET": "signing-test-secret",
    "BASE_URL": "http://localhost:3002",
}
DISPATCH = {
    "AGENT_DISPATCH_URL": "http://dispatcher.example.test:8770",
    "AGENT_DISPATCH_TOKEN": "test-dispatch-token",
    "AGENT_DISPATCH_EVENTS_SECRET": "dispatch-events-secret",
}
SLACK_SECRET = b"signing-test-secret"
EVENTS_SECRET = b"dispatch-events-secret"
THREAD_TS = "1727000000.000100"
RESPONSE_URL = "https://hooks.slack.com/actions/T0TEST1/API/xyz"


@pytest.fixture(autouse=True)
def boundaries(settings):
    settings.SLACK_DELIVERY = dict(CONFIG)
    for key, value in DISPATCH.items():
        setattr(settings, key, value)
    with patch("plane.bgtasks.workitem_realtime._schedule_publish"), patch("celery.app.task.Task.apply_async"):
        yield


@pytest.fixture
def user_data():
    # Brief rule: test data uses @example.com only. Distinct from the board
    # fixture's member@example.com so both users can coexist in one test.
    return {"email": "session@example.com", "password": "test-password", "first_name": "Sam", "last_name": "Session"}


class FakeSlack:
    """SlackClient stand-in: records posts, answers users.info from PROFILES."""

    def __init__(self, profiles=None):
        self.posts = []
        self.ephemerals = []
        self.profiles = profiles or {}

    def install(self):
        stack = ExitStack()
        for patcher in (
            patch.object(SlackClient, "post_message", self.post_message),
            patch.object(SlackClient, "post_ephemeral", self.post_ephemeral),
            patch.object(SlackClient, "user_info", self.user_info),
        ):
            stack.enter_context(patcher)
        return stack

    def post_message(self, token, channel, blocks, text, *, thread_ts=None, client_msg_id=None):
        ts = f"1727000{200 + len(self.posts)}.000001"
        self.posts.append(
            {
                "channel": channel,
                "blocks": blocks,
                "text": text,
                "thread_ts": thread_ts,
                "ts": ts,
                "client_msg_id": client_msg_id,
            }
        )
        return {"ok": True, "ts": ts, "channel": channel}

    def post_ephemeral(self, token, channel, user, text, *, thread_ts=None):
        self.ephemerals.append({"channel": channel, "user": user, "text": text, "thread_ts": thread_ts})
        return {"ok": True}

    def user_info(self, token, user_id):
        return self.profiles.get(user_id) or {"id": user_id, "profile": {"email": "member@example.com"}}


class FakeDispatcher:
    """agent_dispatch.requests stand-in implementing /v1/jobs and subpaths.

    Mirrors the connector spec: POST /v1/jobs is idempotent on
    idempotency_key (same key returns the same job), unknown jobs 404.
    """

    def __init__(self):
        self.calls = []
        self.jobs = {}
        self.counter = 0
        # path segments ("messages"/"cancel") that answer 409 like the live
        # dispatcher does for finished jobs
        self.conflicts = set()

    def __call__(self):
        return patch.object(agent_dispatch.requests, "post", self.post)

    def post(self, url, json=None, **kwargs):
        self.calls.append({"url": url, "json": json, "headers": kwargs.get("headers", {})})
        path = urlsplit(url).path
        if path == "/v1/jobs":
            key = json.get("idempotency_key")
            if key in self.jobs:
                return self._response(200, {"job_id": self.jobs[key], "status": "routing"})
            self.counter += 1
            job_id = f"job-{self.counter}"
            self.jobs[key] = job_id
            return self._response(201, {"job_id": job_id, "status": "routing"})
        found = re.fullmatch(r"/v1/jobs/([A-Za-z0-9._-]+)/messages", path)
        if found:
            if "messages" in self.conflicts:
                return self._response(409, {"error": "job is completed"})
            if found[1] not in self.jobs.values():
                return self._response(404, {"error": "unknown job"})
            return self._response(200, {"ok": True})
        found = re.fullmatch(r"/v1/jobs/([A-Za-z0-9._-]+)/cancel", path)
        if found:
            if "cancel" in self.conflicts:
                return self._response(409, {"error": "job is completed"})
            if found[1] not in self.jobs.values():
                return self._response(404, {"error": "unknown job"})
            return self._response(200, {"ok": True, "status": "cancelling"})
        return self._response(404, {"error": "not found"})

    def _response(self, status_code, body):
        return SimpleNamespace(status_code=status_code, json=lambda: body)

    def of_kind(self, kind):
        return [call for call in self.calls if call["url"].endswith(kind)]

    def bodies(self, kind):
        return [call["json"] for call in self.of_kind(kind)]


@pytest.fixture
def board(user_data):
    owner = User.objects.create(email="owner@example.com", username="owner@example.com", first_name="Olive")
    workspace = Workspace.objects.create(name="Dispatch", slug=f"dispatch-{uuid4().hex[:6]}", owner=owner)
    member = User.objects.create(
        email="member@example.com", username="member@example.com", first_name="Mona", last_name="Member"
    )
    WorkspaceMember.objects.create(workspace=workspace, member=owner, role=20, is_active=True)
    WorkspaceMember.objects.create(workspace=workspace, member=member, role=15, is_active=True)
    project = Project.objects.create(name="Dispatched", identifier="DEV", workspace=workspace)
    ProjectMember.objects.create(project=project, member=member, role=15, is_active=True)
    State.objects.create(name="Todo", group="unstarted", color="#555555", project=project, sequence=15000, default=True)
    State.objects.create(name="In Progress", group="started", color="#F59E0B", project=project, sequence=25000)
    State.objects.create(name="In Review", group="started", color="#8B5CF6", project=project, sequence=30000)
    issue = Issue.objects.create(name="Build the dispatch bridge", project=project)
    connection = SlackConnection.objects.create(
        workspace=workspace,
        team_id="T0TEST1",
        team_name="Team",
        team_domain="team",
        slack_user_id="U0AUTHOR",
        bot_user_id="U0BOT001",
        bot_token_encrypted=encrypt_token("xoxb-test-token"),
        connected_by=owner,
    )
    mapping = SlackChannelMapping.objects.create(
        connection=connection, project=project, channel_id="C0CHANNEL", channel_name="general"
    )
    return SimpleNamespace(
        workspace=workspace,
        owner=owner,
        member=member,
        project=project,
        issue=issue,
        connection=connection,
        mapping=mapping,
    )


def make_link(board, **overrides):
    defaults = dict(
        connection=board.connection,
        issue=board.issue,
        team_id="T0TEST1",
        channel_id="C0CHANNEL",
        thread_ts=THREAD_TS,
        requester_slack_user_id="U0ACTOR1",
        requester=board.member,
        job_id="job-1",
        idempotency_key=f"key-{uuid4().hex[:8]}",
        status="active",
    )
    defaults.update(overrides)
    return SlackAgentJob.objects.create(**defaults)


# --- trigger payload helpers (same shapes Slack posts) ---


def command_payload(board, text, *, trigger_id="trig-1", user_id="U0ACTOR1", channel_id="C0CHANNEL"):
    return {
        "command": "/plane",
        "team_id": "T0TEST1",
        "channel_id": channel_id,
        "user_id": user_id,
        "text": text,
        "response_url": RESPONSE_URL,
        "trigger_id": trigger_id,
    }


def button_payload(board, action_id, value, *, user_id="U0ACTOR1", action_ts="1727000100.000001"):
    return {
        "type": "block_actions",
        "team": {"id": "T0TEST1"},
        "user": {"id": user_id},
        "channel": {"id": "C0CHANNEL"},
        "response_url": RESPONSE_URL,
        "actions": [{"action_id": action_id, "value": value, "action_ts": action_ts, "block_id": "b1"}],
    }


def run_command(board, text, *, trigger_id="trig-1", profiles=None):
    """The slash command through the celery task (its production caller), with
    fake Slack and dispatcher; returns (ack_body, link, slack, dispatcher)."""
    slack = FakeSlack(profiles)
    dispatcher = FakeDispatcher()
    posted = {}
    with slack.install(), dispatcher(), patch(
        "plane.app.slack_delivery.client.post_response_url",
        side_effect=lambda url, body: posted.update(url=url, body=body) or True,
    ):
        run_slack_command.run(command_payload(board, text, trigger_id=trigger_id))
        link = SlackAgentJob.objects.order_by("-created_at").first()
        if link is not None:
            agent_dispatch.dispatch_link(str(link.id))
    if link is not None:
        link.refresh_from_db()
    return posted.get("body") or {}, link, slack, dispatcher


def run_button(board, payload, *, profiles=None):
    """One block_actions payload through the celery task (its production
    caller), plus the dispatch worker when a new link is pending."""
    slack = FakeSlack(profiles)
    dispatcher = FakeDispatcher()
    posted = {}
    with slack.install(), dispatcher(), patch(
        "plane.app.slack_delivery.interactivity.post_response_url",
        side_effect=lambda url, body: posted.update(url=url, body=body) or True,
    ):
        run_slack_interactivity.run(payload)
        link = SlackAgentJob.objects.order_by("-created_at").first()
        if link is not None and link.status == "dispatching" and not link.job_id:
            agent_dispatch.dispatch_link(str(link.id))
    if link is not None:
        link.refresh_from_db()
    return posted.get("body"), link, slack, dispatcher


# --- entity surface ---


def test_entity_card_carries_dispatch_button(board):
    actions = entities.entity_actions(board.issue)
    dispatch = next(action for action in actions["overflow_actions"] if action["action_id"] == "plane:dispatch-agent")
    assert dispatch["value"] == str(board.issue.id)
    assert len(actions["overflow_actions"]) <= 5 and len(actions["primary_actions"]) <= 2


# --- slash command ---


def test_dispatch_requires_project_member(board):
    ProjectMember.objects.filter(project=board.project, member=board.member).update(role=5)
    ack, _link, _slack, dispatcher = run_command(board, f"dispatch DEV-{board.issue.sequence_id}")
    assert ack["response_type"] == "ephemeral"
    assert "not an active member" in ack["text"]
    assert dispatcher.calls == []
    assert SlackAgentJob.objects.count() == 0


def test_dispatch_unconfigured_refusal(board, settings):
    settings.AGENT_DISPATCH_URL = ""
    ack, _link, _slack, dispatcher = run_command(board, f"dispatch DEV-{board.issue.sequence_id}")
    assert "not configured" in ack["text"]
    assert dispatcher.calls == []
    assert SlackAgentJob.objects.count() == 0


def test_command_dispatch_full_flow(board):
    key = f"DEV-{board.issue.sequence_id}"
    ack, link, slack, dispatcher = run_command(board, f"dispatch {key} use the V2 repo", trigger_id="trig-full")
    assert ack["response_type"] == "ephemeral"
    assert "Dispatching DEV-" in ack["text"]
    assert SlackAgentJob.objects.count() == 1
    assert link.job_id == "job-1"
    assert link.status == "active"
    assert link.requester == board.member
    assert link.instructions == "use the V2 repo"
    assert link.thread_ts.startswith("17270002")
    job_calls = dispatcher.of_kind("/v1/jobs")
    assert len(job_calls) == 1
    body = job_calls[0]["json"]
    assert body["source"]["kind"] == "plane"
    assert body["source"]["ticket"] == key
    assert body["source"]["issue_id"] == str(board.issue.id)
    assert body["source"]["workspace"] == board.workspace.slug
    assert body["source"]["project_id"] == str(board.project.id)
    assert body["source"]["url"].startswith("http://localhost:3002/")
    assert body["requester"] == {
        "slack_team_id": "T0TEST1",
        "slack_user_id": "U0ACTOR1",
        "plane_user_id": str(board.member.id),
        "display_name": "Mona Member",
    }
    assert body["reply_to"] == {"channel_id": "C0CHANNEL", "thread_ts": link.thread_ts}
    assert body["routing"] == {"mode": "auto"}
    assert body["instructions"] == "use the V2 repo"
    assert body["preview"] is True
    assert body["idempotency_key"] == "trig-full"
    assert job_calls[0]["headers"]["Authorization"] == "Bearer test-dispatch-token"
    # The anchor is a fresh root message carrying a cancel button.
    anchors = [post for post in slack.posts if post["thread_ts"] is None]
    assert any("Dispatching" in post["blocks"][0]["text"]["text"] for post in anchors)
    buttons = [element for post in anchors for block in post["blocks"] if block["type"] == "actions" for element in block["elements"]]
    assert any(button["action_id"] == "plane:agent-cancel" for button in buttons)
    assert json.loads(buttons[0]["value"])["j"] == str(link.id)


def test_dispatch_retry_with_same_trigger_id_is_idempotent(board):
    text = f"dispatch DEV-{board.issue.sequence_id}"
    _ack1, _link1, _slack1, dispatcher1 = run_command(board, text, trigger_id="trig-dup")
    _ack2, link2, _slack2, dispatcher2 = run_command(board, text, trigger_id="trig-dup")
    assert SlackAgentJob.objects.count() == 1
    assert link2.job_id == "job-1"
    # The job was created exactly once, on the first command; the retry's
    # worker no-ops on the already-active link.
    assert len(dispatcher1.of_kind("/v1/jobs")) == 1
    assert dispatcher2.of_kind("/v1/jobs") == []


def test_dispatch_without_instructions(board):
    _ack, link, _slack, dispatcher = run_command(board, f"dispatch DEV-{board.issue.sequence_id}")
    assert link.instructions == ""
    assert dispatcher.bodies("/v1/jobs")[0]["instructions"] == ""


def test_command_endpoint_passes_trigger_id(board, session_client):
    fields = {
        "command": "/plane",
        "team_id": "T0TEST1",
        "channel_id": "C0CHANNEL",
        "user_id": "U0ACTOR1",
        "text": f"dispatch DEV-{board.issue.sequence_id}",
        "trigger_id": "trig-endpoint",
    }
    raw = urlencode(fields).encode()
    stamp = str(int(time.time()))
    signature = "v0=" + hmac.new(SLACK_SECRET, f"v0:{stamp}:".encode() + raw, hashlib.sha256).hexdigest()
    with patch("plane.app.slack_delivery.api.run_slack_command") as task:
        response = session_client.post(
            "/api/slack-delivery/commands/",
            data=raw,
            content_type="application/x-www-form-urlencoded",
            HTTP_X_SLACK_REQUEST_TIMESTAMP=stamp,
            HTTP_X_SLACK_SIGNATURE=signature,
        )
    assert response.status_code == 200
    queued = task.delay.call_args[0][0]
    assert queued["trigger_id"] == "trig-endpoint"


# --- dispatch button ---


def test_button_dispatch(board):
    ack, link, _slack, dispatcher = run_button(
        board, button_payload(board, "plane:dispatch-agent", str(board.issue.id))
    )
    assert link.job_id == "job-1"
    assert link.status == "active"
    assert link.idempotency_key == "1727000100.000001"
    assert "Dispatching DEV-" in ack["text"]


def test_button_dispatch_same_action_ts_is_idempotent(board):
    slack = FakeSlack()
    dispatcher = FakeDispatcher()
    with slack.install(), dispatcher(), patch(
        "plane.app.slack_delivery.interactivity.post_response_url", side_effect=lambda url, body: True
    ):
        run_slack_interactivity.run(button_payload(board, "plane:dispatch-agent", str(board.issue.id)))
        run_slack_interactivity.run(button_payload(board, "plane:dispatch-agent", str(board.issue.id)))
        agent_dispatch.dispatch_link(str(SlackAgentJob.objects.get().id))
    assert SlackAgentJob.objects.count() == 1
    assert len(dispatcher.of_kind("/v1/jobs")) == 1


def test_button_dispatch_non_member_refused(board):
    ProjectMember.objects.filter(project=board.project, member=board.member).update(role=5)
    ack, _link, _slack, dispatcher = run_button(
        board, button_payload(board, "plane:dispatch-agent", str(board.issue.id))
    )
    assert "not an active member" in ack["text"]
    assert SlackAgentJob.objects.count() == 0
    assert dispatcher.calls == []


# --- events endpoint: transport ---


def agent_event(etype, *, event_id=None, job_id="job-1", data=None):
    return {
        "event_id": event_id or f"evt-{uuid4().hex[:10]}",
        "type": etype,
        "job_id": job_id,
        "at": "2026-10-01T18:00:00Z",
        "reply_to": {"channel_id": "C0CHANNEL", "thread_ts": THREAD_TS},
        "source": {"ticket": "DEV-1", "issue_id": "not-validated-here"},
        "data": data or {},
    }


def signed_agent_event(payload, *, secret=EVENTS_SECRET, stamp=None):
    raw = json.dumps(payload).encode()
    ts = str(int(time.time()) if stamp is None else stamp)
    signature = "v1=" + hmac.new(secret, ts.encode() + b"." + raw, hashlib.sha256).hexdigest()
    return raw, ts, signature


def deliver_event(session_client, payload, *, secret=EVENTS_SECRET, stamp=None, signature=None):
    raw, ts, sig = signed_agent_event(payload, secret=secret, stamp=stamp)
    return session_client.post(
        "/api/slack-delivery/agent-events/",
        data=raw,
        content_type="application/json",
        HTTP_X_AGENT_DISPATCH_TIMESTAMP=ts,
        HTTP_X_AGENT_DISPATCH_SIGNATURE=signature or sig,
    )


def test_events_endpoint_signature_required(board, session_client):
    link = make_link(board)
    payload = agent_event("job.progress", data={"text": "hi"})
    assert deliver_event(session_client, payload, signature="v1=" + "0" * 64).status_code == 403
    stale = int(time.time()) - 301
    assert deliver_event(session_client, payload, stamp=stale).status_code == 403
    wrong_secret = deliver_event(session_client, payload, secret=b"not-the-secret")
    assert wrong_secret.status_code == 403
    assert link.status == "active"  # nothing applied


def test_events_endpoint_unconfigured(board, session_client, settings):
    settings.AGENT_DISPATCH_EVENTS_SECRET = ""
    assert deliver_event(session_client, agent_event("job.progress")).status_code == 503


def test_event_replay_is_deduped(board, session_client):
    make_link(board)
    slack = FakeSlack()
    payload = agent_event("job.progress", data={"text": "halfway there"})
    with slack.install():
        first = deliver_event(session_client, payload)
        second = deliver_event(session_client, payload)
    assert first.status_code == 200 and first.json()["status"] == "processed"
    assert second.status_code == 200 and second.json()["status"] == "duplicate"
    assert len(slack.posts) == 1
    delivery = SlackEventDelivery.objects.get(id=payload["event_id"])
    assert delivery.status == "processed"


def test_event_id_reuse_with_different_body_rejected(board, session_client):
    make_link(board)
    event_id = f"evt-{uuid4().hex[:10]}"
    with FakeSlack().install():
        assert deliver_event(session_client, agent_event("job.progress", event_id=event_id, data={"text": "a"})).status_code == 200
        conflict = deliver_event(session_client, agent_event("job.progress", event_id=event_id, data={"text": "b"}))
    assert conflict.status_code == 409


def test_event_malformed_envelope_rejected(board, session_client):
    make_link(board)
    assert deliver_event(session_client, {"event_id": "bad", "type": "job.progress"}).status_code == 400
    assert deliver_event(session_client, {"event_id": f"evt-{uuid4().hex[:10]}", "type": "nope", "job_id": "job-1"}).status_code == 400
    assert deliver_event(session_client, {"event_id": f"evt-{uuid4().hex[:10]}", "type": "job.progress", "job_id": "bad id!"}).status_code == 400


def test_event_for_unknown_job_is_processed_without_render(board, session_client):
    delivered = deliver_event(session_client, agent_event("job.progress", job_id="job-ghost", data={"text": "hi"}))
    assert delivered.status_code == 200
    assert delivered.json()["status"] == "processed"


# --- events endpoint: rendering per type ---


def render(board, session_client, etype, data, *, slack=None, thread_ts=THREAD_TS, job_id="job-1"):
    link = make_link(board, thread_ts=thread_ts, job_id=job_id)
    event = agent_event(etype, data=data, job_id=job_id)
    with (slack or FakeSlack()).install():
        response = deliver_event(session_client, event)
    link.refresh_from_db()
    return link, response, event


def test_event_routed_renders_routing_line(board, session_client):
    slack = FakeSlack()
    link, response, _event = render(
        board, session_client, "job.routed",
        {"folder": "infra", "harness": "zcode", "model": "glm-5", "confidences": {"harness": 0.8}, "complexity": 2},
        slack=slack,
    )
    assert response.status_code == 200
    assert len(slack.posts) == 1
    text = slack.posts[0]["blocks"][0]["text"]["text"]
    assert "Routed to zcode" in text and "glm-5" in text and "folder infra" in text and "complexity 2" in text
    assert link.last_event_id != ""


def test_event_started_renders_working_line(board, session_client):
    slack = FakeSlack()
    render(board, session_client, "job.started", {"session_id": "s1", "worktree": "/wt", "branch": "feat/aek-276-x"}, slack=slack)
    assert "Agent working" in slack.posts[0]["blocks"][0]["text"]["text"]
    assert "feat/aek-276-x" in slack.posts[0]["blocks"][0]["text"]["text"]


def test_event_progress_renders_text(board, session_client):
    slack = FakeSlack()
    render(board, session_client, "job.progress", {"text": "reading the auth module <script>"}, slack=slack)
    text = slack.posts[0]["blocks"][0]["text"]["text"]
    assert "reading the auth module script" in text
    assert "<" not in text


def test_event_question_renders_option_buttons(board, session_client):
    slack = FakeSlack()
    render(
        board, session_client, "job.question",
        {"question_id": "q_1", "text": "Which repo?", "options": ["V2 repo", "legacy repo"], "timeout_at": "2026-10-01T18:15:00Z"},
        slack=slack,
    )
    post = slack.posts[0]
    assert "Which repo?" in post["blocks"][0]["text"]["text"]
    actions = next(block for block in post["blocks"] if block["type"] == "actions")
    ids = [element["action_id"] for element in actions["elements"]]
    assert ids.count("plane:agent-answer") == 2
    assert ids.count("plane:agent-cancel") == 1
    first = actions["elements"][0]
    value = json.loads(first["value"])
    assert value["q"] == "q_1" and value["o"] == "V2 repo" and value["j"]
    context = next(block for block in post["blocks"] if block["type"] == "context")
    assert "Reply in this thread" in context["elements"][0]["text"]
    assert post["thread_ts"] == THREAD_TS


def test_event_question_caps_options(board, session_client):
    slack = FakeSlack()
    render(board, session_client, "job.question", {"question_id": "q_2", "text": "pick", "options": [f"opt{i}" for i in range(8)]}, slack=slack)
    actions = next(block for block in slack.posts[0]["blocks"] if block["type"] == "actions")
    answers = [element for element in actions["elements"] if element["action_id"] == "plane:agent-answer"]
    assert len(answers) == 6  # 5 options + the Other… fallback
    assert answers[-1]["text"]["text"] == "Other…"


PREVIEW_DATA = {
    "app_url": "https://job-1.preview.gsdut.dev",
    "watch_url": "https://job-1.preview.gsdut.dev/watch",
    "expires_at": "2026-10-01T20:00:00Z",
}


def test_event_preview_ready_ephemeral_to_requester_plus_button(board, session_client):
    slack = FakeSlack()
    link, response, _event = render(board, session_client, "job.preview_ready", PREVIEW_DATA, slack=slack)
    assert response.status_code == 200
    # Links registered for the button, ephemeral to the requester…
    assert link.preview == PREVIEW_DATA
    assert len(slack.ephemerals) == 1
    sent = slack.ephemerals[0]
    assert sent["user"] == "U0ACTOR1" and sent["thread_ts"] == THREAD_TS
    assert "job-1.preview.gsdut.dev/watch" in sent["text"]
    # …and exactly one in-thread notice that carries NO urls, just the button.
    assert len(slack.posts) == 1
    post = slack.posts[0]
    rendered = str(post["blocks"])
    assert "preview.gsdut.dev" not in rendered
    actions = next(block for block in post["blocks"] if block["type"] == "actions")
    button = actions["elements"][0]
    assert button["action_id"] == "plane:agent-preview"
    assert json.loads(button["value"])["j"] == str(link.id)


def test_preview_button_serves_links_to_project_member(board):
    link = make_link(board, preview=dict(PREVIEW_DATA))
    colleague = User.objects.create(email="colleague@example.com", username="colleague@example.com", first_name="Col")
    WorkspaceMember.objects.create(workspace=board.workspace, member=colleague, role=15, is_active=True)
    ProjectMember.objects.create(project=board.project, member=colleague, role=15, is_active=True)
    profiles = {"U0MEMBER2": {"id": "U0MEMBER2", "profile": {"email": "colleague@example.com"}}}
    slack = FakeSlack(profiles)
    with slack.install():
        run_slack_interactivity.run(
            button_payload(None, "plane:agent-preview", json.dumps({"j": str(link.id)}), user_id="U0MEMBER2")
        )
    assert len(slack.ephemerals) == 1
    sent = slack.ephemerals[0]
    assert sent["user"] == "U0MEMBER2"
    assert "job-1.preview.gsdut.dev/watch" in sent["text"]


def test_preview_button_stranger_silently_ignored(board):
    link = make_link(board, preview=dict(PREVIEW_DATA))
    stranger = User.objects.create(email="outsider@example.com", username="outsider@example.com", first_name="Out")
    WorkspaceMember.objects.create(workspace=board.workspace, member=stranger, role=15, is_active=True)
    profiles = {"U0STRANGER": {"id": "U0STRANGER", "profile": {"email": "outsider@example.com"}}}
    slack = FakeSlack(profiles)
    with slack.install():
        with patch("plane.app.slack_delivery.interactivity.post_response_url") as respond:
            run_slack_interactivity.run(
                button_payload(None, "plane:agent-preview", json.dumps({"j": str(link.id)}), user_id="U0STRANGER")
            )
    assert slack.ephemerals == []
    assert respond.call_count == 0


def test_preview_button_still_works_after_completion(board):
    link = make_link(board, preview=dict(PREVIEW_DATA), status="completed")
    slack = FakeSlack()
    with slack.install():
        run_slack_interactivity.run(
            button_payload(None, "plane:agent-preview", json.dumps({"j": str(link.id)}), user_id="U0ACTOR1")
        )
    assert len(slack.ephemerals) == 1
    assert "job-1.preview.gsdut.dev" in slack.ephemerals[0]["text"]


def test_event_completed_comments_and_moves_to_review(board, session_client):
    slack = FakeSlack()
    link, response, _event = render(
        board, session_client, "job.completed",
        {"summary": "Shipped the bridge & tests", "branch": "feat/dev-1-bridge", "commits": ["abc123"], "session_id": "s9"},
        slack=slack,
    )
    assert response.status_code == 200
    board.issue.refresh_from_db()
    assert board.issue.state.name == "In Review"
    comment = IssueComment.objects.get(issue=board.issue)
    assert "Agent run completed" in comment.comment_html
    assert "Shipped the bridge &amp; tests" in comment.comment_html
    assert "feat/dev-1-bridge" in comment.comment_html
    text = slack.posts[0]["blocks"][0]["text"]["text"]
    assert "Agent finished" in text and "Shipped the bridge & tests" in text and "In Review" in text
    assert link.status == "completed"


def test_event_completed_escapes_html_summary(board, session_client):
    render(board, session_client, "job.completed", {"summary": "did <b>not</b> break prod & shipped"}, slack=FakeSlack())
    comment = IssueComment.objects.get(issue=board.issue)
    assert "<b>" not in comment.comment_html
    assert "&lt;b&gt;not&lt;/b&gt;" in comment.comment_html


def test_event_completed_without_review_state(board, session_client):
    State.objects.filter(project=board.project, name="In Review").delete()
    slack = FakeSlack()
    link, _response, _event = render(
        board, session_client, "job.completed", {"summary": "done", "branch": "b"}, slack=slack
    )
    board.issue.refresh_from_db()
    assert board.issue.state.name == "Todo"
    assert link.status == "completed"
    assert "Agent finished" in slack.posts[0]["blocks"][0]["text"]["text"]


def test_event_preview_state_name_not_matched_as_review(board, session_client):
    State.objects.filter(project=board.project, name="In Review").delete()
    State.objects.create(name="Preview", group="started", color="#123456", project=board.project, sequence=31000)
    render(board, session_client, "job.completed", {"summary": "done"}, slack=FakeSlack())
    board.issue.refresh_from_db()
    assert board.issue.state.name == "Todo"  # "Preview" is not a Review state


def test_event_failed_and_cancelled_update_status(board, session_client):
    link, _response, _event = render(board, session_client, "job.failed", {"reason": "agent crashed"})
    assert link.status == "failed"
    link2, _r2, _e2 = render(
        board, session_client, "job.cancelled", {"reason": "user asked"}, thread_ts="1727000000.000101", job_id="job-2"
    )
    assert link2.status == "cancelled"


# --- question answer + cancel buttons ---


def answer_payload(link, *, question_id="q_1", option="V2 repo", user_id="U0ACTOR1", action_ts="1727000300.000001"):
    value = json.dumps({"j": str(link.id), "q": question_id, "o": option})
    return button_payload(None, "plane:agent-answer", value, user_id=user_id, action_ts=action_ts)


def cancel_payload(link, *, user_id="U0ACTOR1"):
    value = json.dumps({"j": str(link.id)})
    return button_payload(None, "plane:agent-cancel", value, user_id=user_id)


def test_answer_button_forwards_question_answer(board):
    link = make_link(board)
    dispatcher = FakeDispatcher()
    dispatcher.jobs["job-1"] = "job-1"  # link made directly; seed the known job
    with FakeSlack().install(), dispatcher():
        with patch(
            "plane.app.slack_delivery.interactivity.post_response_url"
        ) as respond:
            run_slack_interactivity.run(answer_payload(link))
    calls = dispatcher.bodies("/messages")
    assert len(calls) == 1
    assert calls[0]["question_id"] == "q_1"
    assert calls[0]["text"] == "V2 repo"
    assert calls[0]["slack_user_id"] == "U0ACTOR1"
    assert calls[0]["plane_user_id"] == str(board.member.id)
    assert "Answer sent" in respond.call_args[0][1]["text"]


def test_answer_button_from_project_member_allowed(board):
    link = make_link(board)
    other = User.objects.create(email="colleague@example.com", username="colleague@example.com", first_name="Col")
    WorkspaceMember.objects.create(workspace=board.workspace, member=other, role=15, is_active=True)
    ProjectMember.objects.create(project=board.project, member=other, role=15, is_active=True)
    profiles = {"U0OTHER": {"id": "U0OTHER", "profile": {"email": "colleague@example.com"}}}
    dispatcher = FakeDispatcher()
    dispatcher.jobs["job-1"] = "job-1"
    with FakeSlack(profiles).install(), dispatcher():
        with patch("plane.app.slack_delivery.interactivity.post_response_url"):
            run_slack_interactivity.run(answer_payload(link, user_id="U0OTHER"))
    calls = dispatcher.bodies("/messages")
    assert calls[0]["plane_user_id"] == str(other.id)


def test_answer_button_from_stranger_silently_ignored(board):
    link = make_link(board)
    stranger = User.objects.create(email="outsider@example.com", username="outsider@example.com", first_name="Out")
    WorkspaceMember.objects.create(workspace=board.workspace, member=stranger, role=15, is_active=True)
    profiles = {"U0STRANGER": {"id": "U0STRANGER", "profile": {"email": "outsider@example.com"}}}
    dispatcher = FakeDispatcher()
    dispatcher.jobs["job-1"] = "job-1"
    with FakeSlack(profiles).install(), dispatcher():
        with patch(
            "plane.app.slack_delivery.interactivity.post_response_url"
        ) as respond:
            run_slack_interactivity.run(answer_payload(link, user_id="U0STRANGER"))
    assert dispatcher.bodies("/messages") == []
    assert respond.call_count == 0  # silent


def test_cancel_button_calls_dispatcher(board):
    link = make_link(board)
    dispatcher = FakeDispatcher()
    dispatcher.jobs["job-1"] = "job-1"
    with FakeSlack().install(), dispatcher():
        with patch(
            "plane.app.slack_delivery.interactivity.post_response_url"
        ) as respond:
            run_slack_interactivity.run(cancel_payload(link))
    assert len(dispatcher.of_kind("/cancel")) == 1
    link.refresh_from_db()
    assert link.status == "cancelled"
    assert "Cancel sent" in respond.call_args[0][1]["text"]


def test_cancel_before_job_creation(board):
    link = make_link(board, job_id="", status="dispatching")
    dispatcher = FakeDispatcher()
    with FakeSlack().install(), dispatcher():
        with patch("plane.app.slack_delivery.interactivity.post_response_url"):
            run_slack_interactivity.run(cancel_payload(link))
    assert dispatcher.calls == []
    link.refresh_from_db()
    assert link.status == "cancelled"


def test_buttons_after_terminal_job_refused(board):
    link = make_link(board, status="completed")
    dispatcher = FakeDispatcher()
    dispatcher.jobs["job-1"] = "job-1"
    with FakeSlack().install(), dispatcher():
        with patch(
            "plane.app.slack_delivery.interactivity.post_response_url"
        ) as respond:
            run_slack_interactivity.run(answer_payload(link))
            run_slack_interactivity.run(cancel_payload(link))
    assert dispatcher.calls == []
    assert "already finished" in respond.call_args[0][1]["text"]


# --- thread replies → dispatcher ---


def deliver_slack_message(session_client, message, *, channel="C0CHANNEL", dispatcher=None):
    event = {
        "team_id": "T0TEST1",
        "event_id": f"Ev{uuid4().hex[:10]}",
        "type": "event_callback",
        "event": {"type": "message", "channel": channel, **message},
    }
    raw = json.dumps(event).encode()
    stamp = str(int(time.time()))
    signature = "v0=" + hmac.new(SLACK_SECRET, f"v0:{stamp}:".encode() + raw, hashlib.sha256).hexdigest()
    response = session_client.post(
        "/api/slack-delivery/webhooks/",
        data=raw,
        content_type="application/json",
        HTTP_X_SLACK_REQUEST_TIMESTAMP=stamp,
        HTTP_X_SLACK_SIGNATURE=signature,
    )
    assert response.status_code == 202
    delivery = SlackEventDelivery.objects.get(id=event["event_id"])
    dispatcher = dispatcher or FakeDispatcher()
    with dispatcher() as _patch:
        services.process_delivery(str(delivery.id))
    return delivery, dispatcher


def test_thread_reply_from_requester_forwarded(board, session_client):
    make_link(board)
    _delivery, dispatcher = deliver_slack_message(
        session_client,
        {"user": "U0ACTOR1", "text": "use the V2 repo", "ts": "1727000400.000200", "thread_ts": THREAD_TS},
    )
    calls = dispatcher.bodies("/messages")
    assert len(calls) == 1
    assert calls[0]["text"] == "use the V2 repo"
    assert calls[0]["slack_user_id"] == "U0ACTOR1"
    assert calls[0]["plane_user_id"] == str(board.member.id)
    assert calls[0]["ts"] == "1727000400.000200"


def test_thread_reply_from_project_member_forwarded(board, session_client):
    make_link(board)
    colleague = User.objects.create(email="colleague@example.com", username="colleague@example.com", first_name="Col")
    WorkspaceMember.objects.create(workspace=board.workspace, member=colleague, role=15, is_active=True)
    ProjectMember.objects.create(project=board.project, member=colleague, role=15, is_active=True)
    profiles = {"U0COLLEAGUE": {"id": "U0COLLEAGUE", "profile": {"email": "colleague@example.com"}}}
    slack = FakeSlack(profiles)
    with slack.install():
        _delivery, dispatcher = deliver_slack_message(
            session_client,
            {"user": "U0COLLEAGUE", "text": "from a teammate", "ts": "1727000400.000201", "thread_ts": THREAD_TS},
        )
    calls = dispatcher.bodies("/messages")
    assert len(calls) == 1
    assert calls[0]["plane_user_id"] == str(colleague.id)


def test_thread_reply_from_stranger_ignored(board, session_client):
    make_link(board)
    stranger = User.objects.create(email="outsider@example.com", username="outsider@example.com", first_name="Out")
    WorkspaceMember.objects.create(workspace=board.workspace, member=stranger, role=15, is_active=True)
    profiles = {"U0STRANGER": {"id": "U0STRANGER", "profile": {"email": "outsider@example.com"}}}
    with FakeSlack(profiles).install():
        _delivery, dispatcher = deliver_slack_message(
            session_client,
            {"user": "U0STRANGER", "text": "let me in", "ts": "1727000400.000202", "thread_ts": THREAD_TS},
        )
    assert dispatcher.bodies("/messages") == []


def test_thread_reply_unknown_user_ignored(board, session_client):
    make_link(board)
    with FakeSlack({"U0GHOST": {"id": "U0GHOST", "profile": {"email": "ghost@example.com"}}}).install():
        _delivery, dispatcher = deliver_slack_message(
            session_client,
            {"user": "U0GHOST", "text": "who dis", "ts": "1727000400.000203", "thread_ts": THREAD_TS},
        )
    assert dispatcher.bodies("/messages") == []


def test_non_thread_message_ignored(board, session_client):
    make_link(board)
    _delivery, dispatcher = deliver_slack_message(
        session_client,
        {"user": "U0ACTOR1", "text": "not in the thread", "ts": "1727000400.000204"},
    )
    assert dispatcher.bodies("/messages") == []


def test_unmapped_thread_ignored(board, session_client):
    make_link(board)
    _delivery, dispatcher = deliver_slack_message(
        session_client,
        {"user": "U0ACTOR1", "text": "other thread", "ts": "1727000400.000205", "thread_ts": "1726999999.000999"},
    )
    assert dispatcher.bodies("/messages") == []


def test_bot_messages_in_thread_not_forwarded(board, session_client):
    make_link(board)
    _delivery, dispatcher = deliver_slack_message(
        session_client,
        {"user": "U0BOT001", "bot_id": "B0BOT", "text": "🚀 Agent working", "ts": "1727000400.000206", "thread_ts": THREAD_TS},
    )
    assert dispatcher.bodies("/messages") == []


def test_thread_reply_in_channel_without_mapping(board, session_client):
    # Dispatch threads may live in channels with no work-item mapping.
    link = make_link(board, channel_id="C0QUIET")
    _delivery, dispatcher = deliver_slack_message(
        session_client,
        {"user": "U0ACTOR1", "text": "quiet channel reply", "ts": "1727000400.000207", "thread_ts": THREAD_TS},
        channel="C0QUIET",
    )
    calls = dispatcher.bodies("/messages")
    assert len(calls) == 1
    assert calls[0]["text"] == "quiet channel reply"


# --- retry idempotency ---


def test_completed_comment_not_duplicated_on_rerender(board, session_client):
    link = make_link(board)
    event = agent_event("job.completed", data={"summary": "shipped", "branch": "feat/x"})
    with FakeSlack().install():
        first = deliver_event(session_client, event)
    assert first.status_code == 200
    # Simulate a retry reaching the renderer again (e.g. a prior partial
    # failure left the delivery in a retryable state).
    with FakeSlack().install():
        agent_dispatch.render_event(event)
    assert IssueComment.objects.filter(issue=board.issue).count() == 1
    board.issue.refresh_from_db()
    assert board.issue.state.name == "In Review"


def test_thread_posts_carry_client_msg_id(board, session_client):
    slack = FakeSlack()
    _link, _response, event = render(board, session_client, "job.progress", {"text": "halfway"}, slack=slack)
    assert slack.posts[0]["client_msg_id"] == event["event_id"]


def test_reply_forwarded_once_across_redelivery(board, session_client):
    make_link(board)
    message = {"user": "U0ACTOR1", "text": "use the V2 repo", "ts": "1727000400.000210", "thread_ts": THREAD_TS}
    dispatcher1, dispatcher2 = FakeDispatcher(), FakeDispatcher()
    for dispatcher in (dispatcher1, dispatcher2):
        dispatcher.jobs["job-1"] = "job-1"
    with FakeSlack().install(), dispatcher1():
        _d1, _disp = deliver_slack_message(session_client, dict(message), dispatcher=dispatcher1)
    # The awaiting-mapping redelivery path re-processes the same message
    # under a fresh Slack event id; the reply cursor must skip it.
    with FakeSlack().install(), dispatcher2():
        _d2, _disp = deliver_slack_message(session_client, dict(message), dispatcher=dispatcher2)
    assert len(dispatcher1.bodies("/messages")) == 1
    assert dispatcher2.bodies("/messages") == []


def test_newer_reply_still_forwarded_after_cursor(board, session_client):
    make_link(board)
    dispatcher1, dispatcher2 = FakeDispatcher(), FakeDispatcher()
    for dispatcher in (dispatcher1, dispatcher2):
        dispatcher.jobs["job-1"] = "job-1"
    with FakeSlack().install(), dispatcher1():
        deliver_slack_message(
            session_client,
            {"user": "U0ACTOR1", "text": "first", "ts": "1727000400.000211", "thread_ts": THREAD_TS},
            dispatcher=dispatcher1,
        )
    with FakeSlack().install(), dispatcher2():
        deliver_slack_message(
            session_client,
            {"user": "U0ACTOR1", "text": "second", "ts": "1727000400.000212", "thread_ts": THREAD_TS},
            dispatcher=dispatcher2,
        )
    assert len(dispatcher1.bodies("/messages")) == 1
    assert len(dispatcher2.bodies("/messages")) == 1
    assert dispatcher2.bodies("/messages")[0]["text"] == "second"


# --- dispatch watchdog ---


def test_recovery_reenqueues_young_and_fails_stale_links(board):
    from django.utils import timezone

    young = make_link(board, status="dispatching", thread_ts="", job_id="", idempotency_key="rec-young")
    SlackAgentJob.objects.filter(id=young.id).update(created_at=timezone.now() - timedelta(minutes=6))
    stale = make_link(
        board, status="dispatching", thread_ts="", job_id="", idempotency_key="rec-stale", channel_id="C0STALE"
    )
    SlackAgentJob.objects.filter(id=stale.id).update(
        created_at=timezone.now() - timedelta(minutes=31), thread_ts=THREAD_TS
    )
    active = make_link(board, status="active", idempotency_key="rec-active")
    slack = FakeSlack()
    with slack.install(), patch("plane.app.slack_delivery.tasks.run_agent_dispatch") as task:
        agent_dispatch.recover_stuck_links()
    assert task.delay.call_count == 1
    assert task.delay.call_args[0][0] == str(young.id)
    stale.refresh_from_db()
    assert stale.status == "failed"
    active.refresh_from_db()
    assert active.status == "active"
    # The stale link was anchored, so the thread got the failure note.
    notes = [post for post in slack.posts if post["channel"] == "C0STALE"]
    assert len(notes) == 1
    assert "never started" in notes[0]["text"]


def test_recovery_posts_failure_note_to_anchored_thread(board):
    from django.utils import timezone

    stale = make_link(board, status="dispatching", thread_ts=THREAD_TS, idempotency_key="rec-anchored")
    SlackAgentJob.objects.filter(id=stale.id).update(created_at=timezone.now() - timedelta(minutes=45))
    slack = FakeSlack()
    with slack.install(), patch("plane.app.slack_delivery.tasks.run_agent_dispatch"):
        agent_dispatch.recover_stuck_links()
    stale.refresh_from_db()
    assert stale.status == "failed"
    assert len(slack.posts) == 1
    assert "never started" in slack.posts[0]["text"]


# --- dispatcher contract round 2 (live dispatcher, 2026-10-01) ---


def test_event_question_clarify_shape_null_timeout(board, session_client):
    """Dispatcher clarify questions: options=["go"], timeout_at=null."""
    slack = FakeSlack()
    render(
        board, session_client, "job.question",
        {"question_id": "q_clarify", "text": "Which folder should I work in?", "options": ["go"], "timeout_at": None},
        slack=slack,
    )
    post = slack.posts[0]
    context = next(block for block in post["blocks"] if block["type"] == "context")
    assert "Until" not in context["elements"][0]["text"]
    actions = next(block for block in post["blocks"] if block["type"] == "actions")
    assert any(element["text"]["text"] == "go" for element in actions["elements"])


def test_answer_on_finished_job_maps_409(board):
    link = make_link(board)
    dispatcher = FakeDispatcher()
    dispatcher.jobs["job-1"] = "job-1"
    dispatcher.conflicts.add("messages")
    with FakeSlack().install(), dispatcher():
        with patch("plane.app.slack_delivery.interactivity.post_response_url") as respond:
            run_slack_interactivity.run(answer_payload(link))
    assert "already finished" in respond.call_args[0][1]["text"]
    link.refresh_from_db()
    assert link.status == "completed"


def test_cancel_on_finished_job_maps_409(board):
    link = make_link(board)
    dispatcher = FakeDispatcher()
    dispatcher.jobs["job-1"] = "job-1"
    dispatcher.conflicts.add("cancel")
    with FakeSlack().install(), dispatcher():
        with patch("plane.app.slack_delivery.interactivity.post_response_url") as respond:
            run_slack_interactivity.run(cancel_payload(link))
    assert "already finished" in respond.call_args[0][1]["text"]
    link.refresh_from_db()
    assert link.status == "completed"


def test_thread_reply_on_finished_job_marks_completed_silently(board, session_client):
    link = make_link(board)
    dispatcher = FakeDispatcher()
    dispatcher.jobs["job-1"] = "job-1"
    dispatcher.conflicts.add("messages")
    with FakeSlack().install(), dispatcher():
        deliver_slack_message(
            session_client,
            {"user": "U0ACTOR1", "text": "late reply", "ts": "1727000400.000220", "thread_ts": THREAD_TS},
            dispatcher=dispatcher,
        )
    link.refresh_from_db()
    assert link.status == "completed"


def test_completed_renders_commits_in_thread_and_comment(board, session_client):
    slack = FakeSlack()
    render(
        board, session_client, "job.completed",
        {"summary": "done", "branch": "feat/dev-1-x", "commits": ["abc1234 add the bridge", "def5678 fix tests"]},
        slack=slack,
    )
    thread = str(slack.posts[0]["blocks"])
    assert "• abc1234 add the bridge" in thread
    assert "• def5678 fix tests" in thread
    comment = IssueComment.objects.get(issue=board.issue)
    assert "<li>abc1234 add the bridge</li>" in comment.comment_html
    assert "<li>def5678 fix tests</li>" in comment.comment_html


def test_routing_config_env_override(board, settings):
    from django.core.exceptions import ImproperlyConfigured  # noqa: F401 (import guard parity)

    settings.AGENT_DISPATCH_ROUTING = ""
    assert agent_dispatch.routing_config() == {"mode": "auto"}
    settings.AGENT_DISPATCH_ROUTING = "not json"
    assert agent_dispatch.routing_config() == {"mode": "auto"}
    manual = {"mode": "manual", "harness": "cdx", "cwd": "/workspace/gsd-plane/plane"}
    settings.AGENT_DISPATCH_ROUTING = json.dumps(manual)
    assert agent_dispatch.routing_config() == manual


def test_dispatch_payload_carries_env_routing(board, settings):
    manual = {"mode": "manual", "harness": "cdx", "model": "gpt-6-luna", "cwd": "/workspace/gsd-plane/plane"}
    settings.AGENT_DISPATCH_ROUTING = json.dumps(manual)
    _ack, _link, _slack, dispatcher = run_command(board, f"dispatch DEV-{board.issue.sequence_id}")
    assert dispatcher.bodies("/v1/jobs")[0]["routing"] == manual


# --- web-origin dispatch (issue-view card) ---


def web_url(board):
    # project_member() resolves by slug; the workspace slug is unique per test.
    return f"/api/workspaces/{board.workspace.slug}/projects/{board.project.id}/issues/{board.issue.id}/agent-dispatch/"


def join_session_user(board):
    """The session_client user joins the project so the endpoints authorize."""
    user = User.objects.get(email="session@example.com")
    WorkspaceMember.objects.get_or_create(
        workspace=board.workspace, member=user, defaults={"role": 15, "is_active": True}
    )
    ProjectMember.objects.get_or_create(
        project=board.project, member=user, defaults={"role": 15, "is_active": True}
    )
    return user


def test_web_dispatch_requires_project_member(board, session_client):
    ProjectMember.objects.filter(project=board.project, member=board.member).delete()
    response = session_client.post(web_url(board), {"instructions": "do it"}, format="json")
    assert response.status_code == 403
    assert SlackAgentJob.objects.count() == 0


def test_web_dispatch_unconfigured(board, session_client, settings):
    settings.AGENT_DISPATCH_URL = ""
    join_session_user(board)
    response = session_client.post(web_url(board), {"instructions": "do it"}, format="json")
    assert response.status_code == 400
    assert "not configured" in response.json()["error"]
    assert SlackAgentJob.objects.count() == 0


def test_web_dispatch_creates_web_origin_run(board, session_client):
    join_session_user(board)
    dispatcher = FakeDispatcher()
    with FakeSlack().install(), dispatcher():
        response = session_client.post(
            web_url(board), {"instructions": "add a marker doc"}, format="json"
        )
    assert response.status_code == 201
    body = response.json()
    assert body["origin"] == "web"
    assert body["status"] == "dispatching"
    assert body["instructions"] == "add a marker doc"
    assert body["requester"] == "Sam Session"
    assert body["events"] == []
    link = SlackAgentJob.objects.get()
    assert link.origin == "web"
    assert link.requester == User.objects.get(email="session@example.com")
    assert link.connection is None
    assert link.channel_id == "" and link.thread_ts == ""
    # The worker runs with no Slack I/O: anchor skipped, job created.
    with FakeSlack().install(), dispatcher():
        agent_dispatch.dispatch_link(str(link.id))
    link.refresh_from_db()
    assert link.job_id == "job-1"
    assert link.status == "active"
    assert link.thread_ts == ""  # web runs never anchor
    payload = dispatcher.bodies("/v1/jobs")[0]
    # The dispatcher requires non-empty reply_to/slack ids; web runs carry
    # deterministic sentinels derived from the link id.
    assert payload["requester"]["slack_user_id"] == f"U{link.id.hex[:10].upper()}"
    assert payload["requester"]["plane_user_id"] == str(User.objects.get(email="session@example.com").id)
    assert payload["reply_to"]["channel_id"] == f"WEB{link.id.hex[:10].upper()}"
    assert payload["reply_to"]["thread_ts"]
    assert payload["idempotency_key"].startswith("web-")

def test_web_event_rendering_logs_without_slack(board, session_client):
    link = make_link(board, origin="web", channel_id="", thread_ts="", requester_slack_user_id="", connection=None)
    slack = FakeSlack()
    with slack.install():
        response = deliver_event(session_client, agent_event("job.started", data={"branch": "feat/x"}))
    assert response.status_code == 200
    assert slack.posts == []  # no Slack surface for web-origin
    link.refresh_from_db()
    assert link.status == "active"
    assert [entry["type"] for entry in link.event_log] == ["job.started"]
    assert link.event_log[0]["branch"] == "feat/x"


def test_web_completed_comments_moves_and_logs(board, session_client):
    link = make_link(board, origin="web", channel_id="", thread_ts="", requester_slack_user_id="", connection=None)
    with FakeSlack().install():
        response = deliver_event(
            session_client,
            agent_event("job.completed", data={"summary": "done thing", "branch": "feat/y", "commits": ["aaa1111 do thing"]}),
        )
    assert response.status_code == 200
    board.issue.refresh_from_db()
    assert board.issue.state.name == "In Review"
    assert IssueComment.objects.filter(issue=board.issue).count() == 1
    link.refresh_from_db()
    assert link.status == "completed"
    types = [entry["type"] for entry in link.event_log]
    assert types == ["job.completed"]
    assert link.event_log[0]["commits"] == ["aaa1111 do thing"]


def test_web_message_endpoint_forwards_answer(board, session_client):
    join_session_user(board)
    link = make_link(board, origin="web", channel_id="", thread_ts="")
    dispatcher = FakeDispatcher()
    dispatcher.jobs["job-1"] = "job-1"
    with FakeSlack().install(), dispatcher():
        response = session_client.post(
            web_url(board) + f"{link.id}/messages/", {"text": "docs/ is fine"}, format="json"
        )
    assert response.status_code == 200
    calls = dispatcher.bodies("/messages")
    assert calls[0]["text"] == "docs/ is fine"
    assert calls[0]["plane_user_id"] == str(User.objects.get(email="session@example.com").id)
    link.refresh_from_db()
    assert [entry["type"] for entry in link.event_log] == ["message.sent"]


def test_web_message_endpoint_maps_409(board, session_client):
    join_session_user(board)
    link = make_link(board, origin="web", channel_id="", thread_ts="")
    dispatcher = FakeDispatcher()
    dispatcher.jobs["job-1"] = "job-1"
    dispatcher.conflicts.add("messages")
    with FakeSlack().install(), dispatcher():
        response = session_client.post(
            web_url(board) + f"{link.id}/messages/", {"text": "hello?"}, format="json"
        )
    assert response.status_code == 409
    assert "already finished" in response.json()["error"]
    link.refresh_from_db()
    assert link.status == "completed"


def test_web_cancel_endpoint(board, session_client):
    join_session_user(board)
    link = make_link(board, origin="web", channel_id="", thread_ts="")
    dispatcher = FakeDispatcher()
    dispatcher.jobs["job-1"] = "job-1"
    with FakeSlack().install(), dispatcher():
        response = session_client.post(web_url(board) + f"{link.id}/cancel/", format="json")
    assert response.status_code == 200
    assert len(dispatcher.of_kind("/cancel")) == 1
    link.refresh_from_db()
    assert link.status == "cancelled"


def test_web_get_lists_runs(board, session_client):
    join_session_user(board)
    make_link(board, origin="web", channel_id="", thread_ts="")
    make_link(board, origin="slack", thread_ts="1727000000.000900")
    response = session_client.get(web_url(board))
    assert response.status_code == 200
    runs = response.json()
    assert len(runs) == 2
    assert {run["origin"] for run in runs} == {"web", "slack"}
