# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
import hashlib
import hmac
import json
import time
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from plane.db.models import Issue, Project, ProjectMember, State, User, Workspace, WorkspaceMember
from plane.db.models.slack_delivery import (
    SlackConnection,
    SlackConnectNonce,
    SlackChannelMapping,
    SlackMessage,
    SlackIssueLink,
    SlackEventDelivery,
    SlackAppSetup,
)
from plane.app.slack_delivery import services
from plane.app.slack_delivery.client import (
    configuration,
    decrypt_secret,
    encrypt_secret,
    encrypt_token,
    environment_key,
)
from plane.app.slack_delivery.tasks import sync_slack_mapping

pytestmark = [pytest.mark.contract, pytest.mark.django_db(transaction=True)]

CONFIG = {
    "CLIENT_ID": "123456.654321",
    "CLIENT_SECRET": "test-secret",
    "SIGNING_SECRET": "signing-test-secret",
    "BASE_URL": "http://localhost:3002",
}


@pytest.fixture(autouse=True)
def boundaries(settings):
    settings.SLACK_DELIVERY = dict(CONFIG)
    with patch("plane.bgtasks.workitem_realtime._schedule_publish"), patch("celery.app.task.Task.apply_async"):
        yield


@pytest.fixture
def board(workspace, create_user):
    project = Project.objects.create(name="Conversations", identifier="DEV", workspace=workspace)
    ProjectMember.objects.create(project=project, member=create_user, role=20, is_active=True)
    state = State.objects.create(name="Todo", group="unstarted", color="#555555", project=project)
    issue = Issue.objects.create(name="Build feature", project=project, state=state)
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
        project=project, issue=issue, connection=connection, mapping=mapping, workspace=workspace, user=create_user
    )


def signed_event(payload, secret=b"signing-test-secret"):
    raw = json.dumps(payload).encode()
    stamp = str(int(time.time()))
    signature = "v0=" + hmac.new(secret, f"v0:{stamp}:".encode() + raw, hashlib.sha256).hexdigest()
    return raw, stamp, signature


def message_event(board, text, ts="1727251200.123456", user="U0MEMBER1", channel="C0CHANNEL", subtype=None):
    event = {"type": "message", "channel": channel, "user": user, "ts": ts, "text": text}
    if subtype:
        event["subtype"] = subtype
    return {
        "team_id": board.connection.team_id,
        "event_id": f"Ev{uuid4().hex[:10]}",
        "type": "event_callback",
        "event": event,
    }


def webhook(client, payload, *, stamp=None, signature=None, secret=b"signing-test-secret"):
    raw, default_stamp, default_signature = signed_event(payload, secret)
    response = client.post(
        "/api/slack-delivery/webhooks/",
        data=raw,
        content_type="application/json",
        HTTP_X_SLACK_REQUEST_TIMESTAMP=stamp or default_stamp,
        HTTP_X_SLACK_SIGNATURE=signature or default_signature,
    )
    return response


def process_latest(board, event_id):
    delivery = SlackEventDelivery.objects.get(id=event_id)
    services.process_delivery(str(delivery.id))
    delivery.refresh_from_db()
    return delivery


# --- configuration honesty ---


@pytest.mark.parametrize("missing", [["CLIENT_ID"], ["CLIENT_SECRET"], ["SIGNING_SECRET"], ["BASE_URL"], []])
def test_status_reports_configuration_without_secrets(session_client, board, settings, missing):
    settings.SLACK_DELIVERY = {key: ("" if key in missing else value) for key, value in CONFIG.items()}
    response = session_client.get(f"/api/workspaces/{board.workspace.slug}/slack-delivery/")
    assert response.status_code == 200
    data = response.json()
    assert data["configured"] is (len(missing) == 0)
    assert set(data["missing_settings"]) == {environment_key(key) for key in missing}
    body = json.dumps(data)
    assert "xoxb" not in body and "test-secret" not in body


# --- connection authorization ---


def test_callback_rejects_foreign_state_and_unauthenticated(api_client, create_user, workspace):
    other = User.objects.create(email="other@plane.so", username="other@plane.so", first_name="Other")
    SlackConnectNonce.objects.create(
        token_hash=hashlib.sha256(b"state-of-other-user-0123456789abcdef").hexdigest(),
        workspace=workspace,
        user=other,
        expires_at=timezone.now() + timezone.timedelta(minutes=10),
    )
    client = APIClient()
    client.force_authenticate(user=create_user)
    response = client.get("/api/slack-delivery/callback/", {"state": "state-of-other-user-0123456789abcdef", "code": "code"})
    assert response.status_code in (401, 403, 404)
    anonymous = api_client.get("/api/slack-delivery/callback/", {"state": "state-of-other-user-0123456789abcdef", "code": "code"})
    assert anonymous.status_code in (401, 403)


def test_callback_consumes_nonce_once(session_client, board, create_user):
    SlackConnectNonce.objects.create(
        token_hash=hashlib.sha256(b"nonce-state-0123456789abcdefghijklmnop").hexdigest(),
        workspace=board.workspace,
        user=create_user,
        expires_at=timezone.now() + timezone.timedelta(minutes=10),
    )
    with patch("plane.app.slack_delivery.api.SlackClient") as client_class:
        client_class.return_value.complete_installation.return_value = (
            {
                "token_type": "bot",
                "access_token": "xoxb-new",
                "bot_user_id": "U0BOT001",
                "team": {"id": "T0NEW001", "name": "Fresh"},
                "authed_user": {"id": "U0AUTHOR"},
            },
            "xoxb-new",
            {"id": "T0NEW001", "name": "Fresh"},
            {"id": "U0AUTHOR"},
        )
        client_class.return_value.team_domain.return_value = "fresh"
        first = session_client.get("/api/slack-delivery/callback/", {"state": "nonce-state-0123456789abcdefghijklmnop", "code": "one"})
        assert first.status_code == 302
        second = session_client.get("/api/slack-delivery/callback/", {"state": "nonce-state-0123456789abcdefghijklmnop", "code": "one"})
        assert second.status_code in (401, 403, 404)
    connection = SlackConnection.objects.get(team_id="T0NEW001")
    assert connection.workspace_id == board.workspace.id
    assert connection.bot_token_encrypted and "xoxb-new" not in connection.bot_token_encrypted


def test_team_cannot_join_two_workspaces(session_client, board, create_user):
    second_workspace = Workspace.objects.create(
        name="Second", slug=f"second-{uuid4().hex[:6]}", owner=create_user
    )
    board.connection.delete()
    SlackConnection.objects.create(
        workspace=second_workspace,
        team_id="T0TEST1",
        team_name="Team",
        slack_user_id="U0AUTHOR",
        bot_user_id="U0BOT001",
        bot_token_encrypted=encrypt_token("xoxb"),
    )
    SlackConnectNonce.objects.create(
        token_hash=hashlib.sha256(b"same-team-attempt-0123456789abcdefghij").hexdigest(),
        workspace=board.workspace,
        user=create_user,
        expires_at=timezone.now() + timezone.timedelta(minutes=10),
    )
    with patch("plane.app.slack_delivery.api.SlackClient") as client_class:
        client_class.return_value.complete_installation.return_value = (
            {
                "token_type": "bot",
                "access_token": "xoxb",
                "bot_user_id": "U0BOT001",
                "team": {"id": "T0TEST1", "name": "Team"},
                "authed_user": {"id": "U0AUTHOR"},
            },
            "xoxb",
            {"id": "T0TEST1", "name": "Team"},
            {"id": "U0AUTHOR"},
        )
        client_class.return_value.team_domain.return_value = "team"
        response = session_client.get("/api/slack-delivery/callback/", {"state": "same-team-attempt-0123456789abcdefghij", "code": "c"})
        assert response.status_code in (401, 403)


# --- webhook ingestion ---


def test_webhook_signature_required(board, session_client):
    payload = message_event(board, "hello")
    raw, good_stamp, good = signed_event(payload)
    assert (
        session_client.post(
            "/api/slack-delivery/webhooks/",
            data=raw,
            content_type="application/json",
            HTTP_X_SLACK_REQUEST_TIMESTAMP=good_stamp,
            HTTP_X_SLACK_SIGNATURE=good,
        ).status_code
        == 202
    )
    stale = str(int(time.time()) - 10_000)
    stale_sig = "v0=" + hmac.new(
        b"signing-test-secret", f"v0:{stale}:".encode() + raw, hashlib.sha256
    ).hexdigest()
    assert (
        session_client.post(
            "/api/slack-delivery/webhooks/",
            data=raw,
            content_type="application/json",
            HTTP_X_SLACK_REQUEST_TIMESTAMP=stale,
            HTTP_X_SLACK_SIGNATURE=stale_sig,
        ).status_code
        == 403
    )
    assert (
        session_client.post(
            "/api/slack-delivery/webhooks/",
            data=raw,
            content_type="application/json",
            HTTP_X_SLACK_REQUEST_TIMESTAMP=good_stamp,
            HTTP_X_SLACK_SIGNATURE="v0=" + "0" * 64,
        ).status_code
        == 403
    )


def test_webhook_challenge_and_unknown_events(session_client, board):
    response = webhook(session_client, {"type": "url_verification", "challenge": "handshake-token"})
    assert response.status_code == 200
    assert response.json()["challenge"] == "handshake-token"
    response = webhook(
        session_client,
        {
            "team_id": board.connection.team_id,
            "event_id": "EvUnknown1",
            "event": {"type": "reaction_added"},
        },
    )
    assert response.status_code == 202
    assert response.json()["status"] == "ignored"
    assert not SlackEventDelivery.objects.filter(id="EvUnknown1").exists()


def test_event_replay_with_different_body_rejected(session_client, board):
    payload = message_event(board, "first")
    assert webhook(session_client, payload).status_code == 202
    payload["event"]["text"] = "mutated"
    assert webhook(session_client, payload).status_code == 409


def test_message_before_connection_waits_then_processes(session_client, board):
    team = board.connection.team_id
    board.connection.delete()
    payload = message_event(board, f"Please review {board.project.identifier}-{board.issue.sequence_id}")
    assert webhook(session_client, payload).status_code == 202
    delivery = SlackEventDelivery.objects.get(id=payload["event_id"])
    assert delivery.status == "waiting"
    assert delivery.payload.get("event", {}).get("text")
    connection = SlackConnection.objects.create(
        workspace=board.workspace,
        team_id=team,
        team_name="Team",
        team_domain="team",
        slack_user_id="U0AUTHOR",
        bot_user_id="U0BOT001",
        bot_token_encrypted=encrypt_token("xoxb"),
    )
    services.process_delivery(str(delivery.id))
    delivery.refresh_from_db()
    assert delivery.status == "awaiting_mapping"
    mapping = SlackChannelMapping.objects.create(
        connection=connection, project=board.project, channel_id="C0CHANNEL", channel_name="general"
    )
    services.process_delivery(str(delivery.id))
    delivery.refresh_from_db()
    assert delivery.status == "processed"
    message = SlackMessage.objects.get(mapping=mapping, ts="1727251200.123456")
    assert SlackIssueLink.objects.filter(message=message, issue=board.issue).exists()


def test_message_auto_links_and_suppression_holds(session_client, board):
    payload = message_event(board, f"closing {board.project.identifier}-{board.issue.sequence_id} today")
    assert webhook(session_client, payload).status_code == 202
    assert process_latest(board, payload["event_id"]).status == "processed"
    message = SlackMessage.objects.get(mapping=board.mapping, ts="1727251200.123456")
    assert SlackIssueLink.objects.filter(message=message, issue=board.issue, is_manual=False).exists()
    link = SlackIssueLink.objects.get(message=message, issue=board.issue)
    link.is_suppressed = True
    link.is_manual = False
    link.save()
    # Identical redelivery is inert.
    services.process_delivery(str(payload["event_id"]))
    assert not SlackIssueLink.objects.filter(message=message, issue=board.issue, is_suppressed=False).exists()


def test_updates_and_deletions_stay_current(session_client, board):
    ts = "1727251201.000001"
    payload = message_event(board, "draft note", ts=ts)
    assert webhook(session_client, payload).status_code == 202
    assert process_latest(board, payload["event_id"]).status == "processed"
    changed = {
        "team_id": board.connection.team_id,
        "event_id": f"Ev{uuid4().hex[:10]}",
        "type": "event_callback",
        "event": {
            "type": "message",
            "subtype": "message_changed",
            "channel": "C0CHANNEL",
            "message": {
                "type": "message",
                "channel": "C0CHANNEL",
                "user": "U0MEMBER1",
                "ts": ts,
                "text": f"edited {board.project.identifier}-{board.issue.sequence_id}",
                "edited": {"user": "U0MEMBER1", "ts": "1727251201.1"},
            },
        },
    }
    assert webhook(session_client, changed).status_code == 202
    assert process_latest(board, changed["event_id"]).status == "processed"
    message = SlackMessage.objects.get(mapping=board.mapping, ts=ts)
    assert f"{board.project.identifier}-{board.issue.sequence_id}" in message.text
    assert SlackIssueLink.objects.filter(message=message, issue=board.issue).exists()
    deleted = {
        "team_id": board.connection.team_id,
        "event_id": f"Ev{uuid4().hex[:10]}",
        "type": "event_callback",
        "event": {"type": "message", "subtype": "message_deleted", "channel": "C0CHANNEL", "deleted_ts": ts},
    }
    assert webhook(session_client, deleted).status_code == 202
    assert process_latest(board, deleted["event_id"]).status == "processed"
    message.refresh_from_db()
    assert message.is_deleted
    assert services.list_issue_messages(board.issue) == []


def test_noise_subtypes_are_skipped(session_client, board):
    payload = message_event(board, "joined", subtype="channel_join")
    assert webhook(session_client, payload).status_code == 202
    assert process_latest(board, payload["event_id"]).status == "processed"
    assert SlackMessage.objects.count() == 0


def test_unmapped_channel_never_blocks_mapped_one(session_client, board):
    payload = message_event(board, "elsewhere", channel="C0OTHERX")
    assert webhook(session_client, payload).status_code == 202
    assert process_latest(board, payload["event_id"]).status == "awaiting_mapping"
    good = message_event(board, f"do {board.project.identifier}-{board.issue.sequence_id}")
    assert webhook(session_client, good).status_code == 202
    assert process_latest(board, good["event_id"]).status == "processed"


def test_malformed_team_rejected_at_ingestion(session_client, board):
    payload = message_event(board, "hello")
    payload["team_id"] = "not-a-team-id!"
    assert webhook(session_client, payload).status_code == 400
    assert not SlackEventDelivery.objects.filter(id=payload["event_id"]).exists()


def test_mismatched_payload_is_terminal(board):
    delivery = SlackEventDelivery.objects.create(
        id="EvTerm001",
        connection=board.connection,
        team_id=board.connection.team_id,
        channel_id="C0CHANNEL",
        event="message",
        body_hash="0" * 64,
        payload={"team_id": "T0MISMATCH", "event": {"type": "message", "channel": "C0CHANNEL", "ts": "1727251209.000001"}},
        status="queued",
    )
    services.process_delivery(str(delivery.id))
    delivery.refresh_from_db()
    assert delivery.status == "failed"
    assert delivery.payload == {}


# --- project and issue surfaces ---


def test_guests_cannot_read_project_conversations(board, create_user):
    guest = User.objects.create(email="guest@plane.so", username="guest@plane.so", first_name="Guest")
    WorkspaceMember.objects.create(workspace=board.workspace, member=guest, role=5, is_active=True)
    client = APIClient()
    client.force_authenticate(user=guest)
    response = client.get(f"/api/workspaces/{board.workspace.slug}/projects/{board.project.id}/slack-delivery/")
    assert response.status_code in (401, 403)


def test_project_and_issue_views(session_client, board):
    SlackMessage.objects.create(
        mapping=board.mapping,
        ts="1727251202.000001",
        user_id="U0MEMBER1",
        text=f"note {board.project.identifier}-{board.issue.sequence_id}",
        posted_at=timezone.now(),
    )
    message = SlackMessage.objects.get(ts="1727251202.000001")
    SlackIssueLink.objects.create(issue=board.issue, message=message)
    project_response = session_client.get(
        f"/api/workspaces/{board.workspace.slug}/projects/{board.project.id}/slack-delivery/"
    )
    assert project_response.status_code == 200
    data = project_response.json()
    assert data["channels"][0]["channel"] == "general"
    assert data["messages"][0]["text"].startswith("note")
    issue_response = session_client.get(
        f"/api/workspaces/{board.workspace.slug}/projects/{board.project.id}/issues/{board.issue.id}/slack-messages/"
    )
    assert issue_response.status_code == 200
    assert issue_response.json()[0]["id"] == str(message.id)


def test_manual_permalink_link_and_unlink(session_client, board):
    permalink = "https://team.slack.com/archives/C0CHANNEL/p1727251203000001"
    foreign = "https://attacker.slack.com/archives/C0CHANNEL/p1727251203000001"
    response = session_client.post(
        f"/api/workspaces/{board.workspace.slug}/projects/{board.project.id}/issues/{board.issue.id}/slack-messages/",
        {"url": foreign},
    )
    assert response.status_code == 400
    with patch("plane.app.slack_delivery.api.SlackClient") as client_class:
        client_class.return_value.fetch_message.return_value = {
            "type": "message",
            "user": "U0MEMBER1",
            "ts": "1727251203.000001",
            "text": f"manual note {board.project.identifier}-{board.issue.sequence_id}",
        }
        response = session_client.post(
            f"/api/workspaces/{board.workspace.slug}/projects/{board.project.id}/issues/{board.issue.id}/slack-messages/",
            {"url": permalink},
        )
    assert response.status_code == 201
    message = SlackMessage.objects.get(ts="1727251203.000001")
    assert SlackIssueLink.objects.get(message=message, issue=board.issue).is_manual
    wrong_channel = "https://team.slack.com/archives/C0UNLINK/p1727251203000001"
    response = session_client.post(
        f"/api/workspaces/{board.workspace.slug}/projects/{board.project.id}/issues/{board.issue.id}/slack-messages/",
        {"url": wrong_channel},
    )
    assert response.status_code in (400, 404)
    response = session_client.delete(
        f"/api/workspaces/{board.workspace.slug}/projects/{board.project.id}/issues/{board.issue.id}/slack-messages/{message.id}/"
    )
    assert response.status_code == 204
    assert not SlackIssueLink.objects.filter(message=message, issue=board.issue, is_suppressed=False).exists()


# --- mapping administration ---


def test_mapping_requires_channel_membership(session_client, board):
    with patch("plane.app.slack_delivery.api.SlackClient") as client_class:
        client_class.return_value.channels.return_value = [
            {"id": "C0MEMBER", "name": "general", "is_private": False, "is_member": True},
            {"id": "C0OUTSID", "name": "secret", "is_private": False, "is_member": False},
        ]
        response = session_client.post(
            f"/api/workspaces/{board.workspace.slug}/slack-delivery/mappings/",
            {
                "connection_id": str(board.connection.id),
                "project_id": str(board.project.id),
                "channel_id": "C0OUTSID",
            },
        )
        assert response.status_code == 403
        response = session_client.post(
            f"/api/workspaces/{board.workspace.slug}/slack-delivery/mappings/",
            {
                "connection_id": str(board.connection.id),
                "project_id": str(board.project.id),
                "channel_id": "C0MEMBER",
            },
        )
        assert response.status_code == 201
        assert response.json()["channel"] == "general"


def test_mapping_conflicts_with_other_project(session_client, board):
    second = Project.objects.create(name="Second", identifier="SEC", workspace=board.workspace)
    ProjectMember.objects.create(project=second, member=board.user, role=20, is_active=True)
    with patch("plane.app.slack_delivery.api.SlackClient") as client_class:
        client_class.return_value.channels.return_value = [
            {"id": "C0CHANNEL", "name": "general", "is_private": False, "is_member": True},
        ]
        response = session_client.post(
            f"/api/workspaces/{board.workspace.slug}/slack-delivery/mappings/",
            {
                "connection_id": str(board.connection.id),
                "project_id": str(second.id),
                "channel_id": "C0CHANNEL",
            },
        )
        assert response.status_code == 400


def test_sync_mapping_history(board):
    with patch("plane.app.slack_delivery.client.SlackClient") as client_class:
        client_class.return_value.channel_history.return_value = [
            {
                "type": "message",
                "user": "U0MEMBER1",
                "ts": "1727251204.000001",
                "text": f"history {board.project.identifier}-{board.issue.sequence_id}",
            },
            {
                "type": "message",
                "subtype": "channel_join",
                "user": "U0MEMBER1",
                "ts": "1727251204.000002",
                "text": "",
            },
        ]
        sync_slack_mapping.run(str(board.mapping.id))
    board.mapping.refresh_from_db()
    assert board.mapping.sync_status == "synced"
    assert SlackMessage.objects.count() == 1
    message = SlackMessage.objects.first()
    assert SlackIssueLink.objects.filter(message=message, issue=board.issue).exists()


# --- release adapter and disconnect ---


def test_conversation_sources_validate_scope(board):
    message = SlackMessage.objects.create(
        mapping=board.mapping, ts="1727251205.000001", user_id="U0MEMBER1", text="evidence"
    )
    sources = services.get_conversation_sources(board.project, [str(message.id)])
    assert sources[0]["type"] == "slack_message"
    assert sources[0]["url"].startswith("https://team.slack.com/archives/")
    with pytest.raises(ValidationError):
        services.get_conversation_sources(board.project, [str(uuid4())])


def test_disconnect_retains_history_and_clears_token(session_client, board):
    SlackMessage.objects.create(mapping=board.mapping, ts="1727251206.000001", user_id="U0", text="kept")
    response = session_client.delete(
        f"/api/workspaces/{board.workspace.slug}/slack-delivery/connections/{board.connection.id}/"
    )
    assert response.status_code == 204
    board.connection.refresh_from_db()
    assert not board.connection.is_active
    assert board.connection.bot_token_encrypted == ""
    assert SlackMessage.objects.count() == 1
    assert services.list_project_messages(board.project)


# --- click-to-connect app setup ---


def setup_url_for(board):
    return f"/api/workspaces/{board.workspace.slug}/slack-delivery/setup/"


def test_setup_view_shape_and_manifest(session_client, board):
    response = session_client.get(setup_url_for(board))
    assert response.status_code == 200
    data = response.json()
    assert data["commands_url"] == "http://localhost:3002/api/slack-delivery/commands/"
    assert data["app"] == {"configured": False, "client_id_masked": "", "updated_at": None}
    manifest = data["manifest"]
    assert manifest["features"]["slash_commands"] == [
        {
            "command": "/plane",
            "url": "http://localhost:3002/api/slack-delivery/commands/",
            "description": "Manage Plane work items",
            "should_escape": False,
        }
    ]
    assert manifest["oauth_config"]["redirect_urls"] == ["http://localhost:3002/api/slack-delivery/callback/"]
    assert manifest["settings"]["event_subscriptions"]["request_url"] == "http://localhost:3002/api/slack-delivery/webhooks/"
    for scope in ("commands", "links:read", "links:write", "users:read.email"):
        assert scope in manifest["oauth_config"]["scopes"]
    assert "link_shared" in manifest["settings"]["event_subscriptions"]["bot_events"]
    assert manifest["features"]["unfurl_domains"] == ["localhost"]
    split = urlsplit(data["setup_url"])
    assert split.scheme == "https" and split.netloc == "api.slack.com" and split.path == "/apps"
    query = parse_qs(split.query)
    assert query["new_app"] == ["1"]
    assert json.loads(query["manifest_json"][0]) == manifest


def test_setup_origin_falls_back_to_request_headers(session_client, board, settings):
    settings.SLACK_DELIVERY = {**CONFIG, "BASE_URL": ""}
    undetermined = session_client.get(setup_url_for(board))
    assert undetermined.status_code == 200
    data = undetermined.json()
    assert data["setup_url"] is None and data["manifest"] is None
    assert data["configuration_error"] == "The public address of this board could not be determined."
    forwarded = session_client.get(
        setup_url_for(board), HTTP_X_FORWARDED_HOST="board.example.com", HTTP_X_FORWARDED_PROTO="https"
    )
    assert forwarded.status_code == 200
    manifest = forwarded.json()["manifest"]
    assert manifest["features"]["slash_commands"][0]["url"] == "https://board.example.com/api/slack-delivery/commands/"
    assert manifest["oauth_config"]["redirect_urls"] == ["https://board.example.com/api/slack-delivery/callback/"]


def test_setup_requires_workspace_admin(board, create_user):
    member = User.objects.create(email="member@plane.so", username="member@plane.so", first_name="Member")
    WorkspaceMember.objects.create(workspace=board.workspace, member=member, role=15, is_active=True)
    client = APIClient()
    client.force_authenticate(user=member)
    assert client.get(setup_url_for(board)).status_code == 403
    assert client.put(setup_url_for(board), {}).status_code == 403


def test_setup_put_validation_and_upsert(session_client, board):
    url = setup_url_for(board)
    payload = {
        "client_id": "123456.654321",
        "client_secret": "not-the-client-secret",
        "signing_secret": "not-the-signing-secret",
    }
    assert session_client.put(url, {**payload, "client_id": "not-a-client-id"}).status_code == 400
    assert session_client.put(url, {**payload, "client_secret": "short"}).status_code == 400
    assert session_client.put(url, {**payload, "app_id": "invalid"}).status_code == 400
    assert SlackAppSetup.objects.count() == 0
    created = session_client.put(url, {**payload, "app_id": "A0TESTAPP"})
    assert created.status_code == 201
    data = created.json()
    assert data["app"]["configured"] is True
    assert data["app"]["client_id_masked"] == "123456.65…"
    assert data["app"]["updated_at"]
    body = json.dumps(data)
    assert "not-the-client-secret" not in body and "not-the-signing-secret" not in body
    assert SlackAppSetup.objects.count() == 1
    row = SlackAppSetup.objects.get()
    assert row.app_id == "A0TESTAPP"
    assert row.created_by_id == board.user.id
    assert decrypt_secret(row.client_secret_encrypted) == "not-the-client-secret"
    assert decrypt_secret(row.signing_secret_encrypted) == "not-the-signing-secret"
    updated = session_client.put(url, {**payload, "app_id": ""})
    assert updated.status_code == 200
    assert updated.json()["app"]["configured"] is True
    assert SlackAppSetup.objects.count() == 1
    assert SlackAppSetup.objects.get().app_id == ""


def test_configuration_precedence_settings_row_env(settings, monkeypatch):
    settings.SLACK_DELIVERY = {}
    for key, value in {
        "SLACK_CLIENT_ID": "999888.777666",
        "SLACK_CLIENT_SECRET": "env-client-secret",
        "SLACK_SIGNING_SECRET": "env-signing-secret",
        "SLACK_APP_BASE_URL": "http://localhost:3003",
    }.items():
        monkeypatch.setenv(key, value)
    assert configuration() == {
        "CLIENT_ID": "999888.777666",
        "CLIENT_SECRET": "env-client-secret",
        "SIGNING_SECRET": "env-signing-secret",
        "BASE_URL": "http://localhost:3003",
    }
    SlackAppSetup.objects.create(
        client_id="123456.654321",
        client_secret_encrypted=encrypt_secret("db-client-secret"),
        signing_secret_encrypted=encrypt_secret("db-signing-secret"),
    )
    assert configuration() == {
        "CLIENT_ID": "123456.654321",
        "CLIENT_SECRET": "db-client-secret",
        "SIGNING_SECRET": "db-signing-secret",
        "BASE_URL": "http://localhost:3003",
    }
    settings.SLACK_DELIVERY = dict(CONFIG)
    assert configuration() == CONFIG


def test_setup_recovers_from_undecryptable_secrets(session_client, board):
    SlackAppSetup.objects.create(
        client_id="123456.654321",
        client_secret_encrypted="not-fernet-ciphertext",
        signing_secret_encrypted=encrypt_secret("db-signing-secret"),
    )
    data = session_client.get(setup_url_for(board)).json()
    assert data["app"]["configured"] is False
    assert data["app"]["client_id_masked"] == "123456.65…"
    response = session_client.put(
        setup_url_for(board),
        {
            "client_id": "123456.654321",
            "client_secret": "fresh-client-secret",
            "signing_secret": "fresh-signing-secret",
        },
    )
    assert response.status_code == 200
    assert response.json()["app"]["configured"] is True
    assert SlackAppSetup.objects.count() == 1
    assert decrypt_secret(SlackAppSetup.objects.get().client_secret_encrypted) == "fresh-client-secret"
