# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
import hashlib
import hmac
import json
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.db import transaction
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from plane.db.models import Issue, Project, ProjectMember, State, User, WorkspaceMember
from plane.db.models.github_delivery import (
    GitHubApp,
    GitHubConnection,
    GitHubConnectNonce,
    GitHubRepositoryMapping,
    GitHubPullRequest,
    GitHubIssueLink,
    GitHubRelease,
    GitHubWebhookDelivery,
)
from plane.app.github_delivery import services
from plane.app.github_delivery.crypto import encrypt_secret
from plane.app.github_delivery.tasks import sync_github_mapping

pytestmark = [pytest.mark.contract, pytest.mark.django_db(transaction=True)]

WEBHOOK_SECRET = "webhook-test-secret"


@pytest.fixture(autouse=True)
def boundaries(settings):
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    settings.GITHUB_DELIVERY = {"BASE_URL": "http://localhost:3002"}
    boundaries.pem = private_key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    with patch("plane.bgtasks.workitem_realtime._schedule_publish"), patch("celery.app.task.Task.apply_async") as queue:
        yield queue


@pytest.fixture
def board(workspace, create_user):
    project = Project.objects.create(name="Development", identifier="DEV", workspace=workspace)
    ProjectMember.objects.create(project=project, member=create_user, role=20, is_active=True)
    state = State.objects.create(name="Todo", group="unstarted", color="#555555", project=project)
    issue = Issue.objects.create(name="Build feature", project=project, state=state)
    app = GitHubApp.objects.create(
        host="github.com",
        app_id=123,
        slug="board-test",
        client_id="Iv.test",
        client_secret=encrypt_secret("test-secret"),
        private_key=encrypt_secret(boundaries.pem),
        webhook_secret=encrypt_secret(WEBHOOK_SECRET),
    )
    connection = GitHubConnection.objects.create(
        workspace=workspace,
        app=app,
        host="github.com",
        installation_id=456,
        account_login="team",
        github_user_id=7,
        connected_by=create_user,
        authorized_repository_ids=[789],
    )
    mapping = GitHubRepositoryMapping.objects.create(
        connection=connection, project=project, repository_id=789, full_name="team/repo", is_private=True
    )
    return SimpleNamespace(
        project=project,
        issue=issue,
        connection=connection,
        mapping=mapping,
        workspace=workspace,
        user=create_user,
        app=app,
    )


def root(board):
    return f"/api/workspaces/{board.workspace.slug}/github-delivery/"


def development(board):
    return f"/api/workspaces/{board.workspace.slug}/projects/{board.project.id}/github-delivery/"


def issue_url(board):
    project = f"/api/workspaces/{board.workspace.slug}/projects/{board.project.id}/"
    return project + f"issues/{board.issue.id}/github-pull-requests/"


def pr_payload(board, **overrides):
    return {
        "id": 900,
        "number": 9,
        "title": f"Build DEV-{board.issue.sequence_id}",
        "body": "Private evidence",
        "base": {"repo": {"id": 789}},
        "head": {"ref": "feature/DEV-999"},
        "state": "open",
        "updated_at": "2026-09-25T12:00:00Z",
        **overrides,
    }


def event(board, **overrides):
    return {
        "installation": {"id": 456},
        "repository": {"id": 789},
        "action": "opened",
        "pull_request": pr_payload(board),
        **overrides,
    }


def webhook(payload, delivery_id=None, event_name="pull_request", signature=None):
    raw = json.dumps(payload).encode()
    signature = signature or "sha256=" + hmac.new(WEBHOOK_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    return APIClient().post(
        "/api/github-delivery/webhooks/",
        raw,
        content_type="application/json",
        HTTP_X_HUB_SIGNATURE_256=signature,
        HTTP_X_GITHUB_DELIVERY=str(delivery_id or uuid4()),
        HTTP_X_GITHUB_EVENT=event_name,
    )


class HTTP:
    def __init__(self, data, status=200):
        self.status_code = status
        self.data = data

    def json(self):
        return self.data


def manifest_credentials():
    return {
        "id": 123,
        "slug": "board-test",
        "client_id": "Iv.test",
        "client_secret": "test-secret",
        "pem": boundaries.pem,
        "webhook_secret": WEBHOOK_SECRET,
    }


def github_http(method, url, **kwargs):
    assert kwargs["allow_redirects"] is False
    path = urlsplit(url).path
    if path.startswith("/api/v3/app-manifests/") or path.startswith("/app-manifests/"):
        assert path.endswith("/conversions")
        return HTTP(manifest_credentials())
    if path == "/login/oauth/access_token":
        return HTTP({"access_token": "transient-user-token"})
    if path == "/user/installations":
        return HTTP({"total_count": 1, "installations": [{"id": 456, "app_id": 123}]})
    if path == "/app/installations/456":
        return HTTP({"id": 456, "app_id": 123, "account": {"login": "team"}})
    if path == "/user/installations/456/repositories":
        return HTTP({"total_count": 1, "repositories": [{"id": 789}]})
    if path == "/user":
        return HTTP({"id": 7})
    if path == "/app/installations/456/access_tokens":
        assert kwargs["json"]["permissions"] == {"metadata": "read", "pull_requests": "read", "contents": "read"}
        return HTTP({"token": "transient-installation-token"})
    if path == "/installation/repositories":
        return HTTP(
            {
                "total_count": 2,
                "repositories": [
                    {"id": 789, "full_name": "team/repo", "private": True},
                    {"id": 999, "full_name": "team/unauthorized", "private": True},
                ],
            }
        )
    raise AssertionError((method, path))


def authorize_state(client, board, host="github.com"):
    """Walk the full click-to-connect chain and return the final authorization state."""
    response = client.post(root(board) + "connect/", {"account_type": "personal"}, format="json")
    assert response.status_code == 200, response.data
    start = urlsplit(response.data["url"])
    assert start.path == "/api/github-delivery/manifest/start/"
    state = parse_qs(start.query)["state"][0]
    page = client.get(start.path, {"state": state})
    assert page.status_code == 200
    assert f'action="https://{host}/settings/apps/new"' in page.content.decode()
    with patch("requests.request", side_effect=github_http):
        response = client.get("/api/github-delivery/manifest/callback/", {"state": state, "code": "manifestcode"})
        assert response.status_code == 302, getattr(response, "data", None)
        install = urlsplit(response.url)
        assert install.path == f"/apps/board-test/installations/new"
        state = parse_qs(install.query)["state"][0]
        response = client.get("/api/github-delivery/setup/", {"state": state, "installation_id": 456})
        assert response.status_code == 302
        authorize = urlsplit(response.url)
        assert authorize.path == "/login/oauth/authorize"
        return parse_qs(authorize.query)["state"][0]


def test_configuration_permissions_and_secure_callback(board, session_client, settings):
    board.mapping.delete()
    board.connection.delete()
    state = authorize_state(session_client, board)
    callback = "/api/github-delivery/callback/"
    assert APIClient().get(callback, {"state": state, "code": "code"}).status_code in (401, 403)
    outsider = User.objects.create(email="outsider@example.test", username="outsider")
    other = APIClient()
    other.force_authenticate(outsider)
    assert other.get(callback, {"state": state, "code": "code"}).status_code == 404
    with patch("requests.request", side_effect=github_http) as http:
        response = session_client.get(callback, {"state": state, "code": "code"})
        assert response.status_code == 302, getattr(response, "data", None)
        assert response.url == "http://localhost:3002/test-workspace/settings/integrations/?github=connected"
        count = http.call_count
        assert session_client.get(callback, {"state": state, "code": "code"}).status_code == 404
        assert http.call_count == count
        board.connection = GitHubConnection.objects.get(installation_id=456)
        assert board.connection.app.slug == "board-test"
        repositories = session_client.get(root(board) + f"connections/{board.connection.id}/repositories/")
        assert [item["id"] for item in repositories.data] == [789]
    board.connection.refresh_from_db()
    assert board.connection.authorized_repository_ids == [789]
    assert board.connection.host == "github.com"
    assert GitHubConnectNonce.objects.filter(consumed_at__isnull=False).count() == 1
    # Credentials are stored encrypted: neither the secret nor the key material appears raw.
    stored = str(list(GitHubApp.objects.values("client_secret", "private_key", "webhook_secret")))
    assert "test-secret" not in stored and "PRIVATE KEY" not in stored and WEBHOOK_SECRET not in stored
    status = session_client.get(root(board))
    assert status.data["connect_urls"]["webhook_url"] == "http://localhost:3002/api/github-delivery/webhooks/"
    assert status.data["connections"][0]["host_display"] == "GitHub"


def test_enterprise_host_click_connect(board, session_client):
    response = session_client.post(
        root(board) + "connect/",
        {"account_type": "enterprise", "enterprise_url": "https://GitHub.Example.com/"},
        format="json",
    )
    assert response.status_code == 200, response.data
    start = urlsplit(response.data["url"])
    state = parse_qs(start.query)["state"][0]
    nonce = GitHubConnectNonce.objects.get(token_hash=hashlib.sha256(state.encode()).hexdigest())
    assert nonce.host == "github.example.com" and nonce.origin == "http://localhost:3002"
    page = session_client.get(start.path, {"state": state})
    assert 'action="https://github.example.com/settings/apps/new"' in page.content.decode()
    assert 'name="manifest"' in page.content.decode()
    with patch("requests.request", side_effect=github_http):
        response = session_client.get("/api/github-delivery/manifest/callback/", {"state": state, "code": "mc"})
        assert response.status_code == 302, getattr(response, "data", None)
        assert urlsplit(response.url).netloc == "github.example.com"
    assert GitHubApp.objects.filter(host="github.example.com", app_id=123).exists()
    # Invalid enterprise addresses are rejected before anything is created.
    assert (
        session_client.post(root(board) + "connect/", {"account_type": "enterprise", "enterprise_url": "nope"}).status_code
        == 400
    )
    assert GitHubApp.objects.count() == 2


def test_forged_installation_nonadmin_and_lost_membership(board, session_client):
    state = authorize_state(session_client, board)

    def denied_http(method, url, **kwargs):
        if urlsplit(url).path == "/user/installations":
            return HTTP({"installations": [{"id": 999, "app_id": 123}], "total_count": 1})
        return github_http(method, url, **kwargs)

    with patch("requests.request", side_effect=denied_http):
        assert session_client.get("/api/github-delivery/callback/", {"state": state, "code": "code"}).status_code == 403
    # A project administrator who is not a workspace administrator may still connect.
    WorkspaceMember.objects.filter(workspace=board.workspace, member=board.user).update(role=15)
    state = authorize_state(session_client, board)
    with patch("requests.request", side_effect=github_http):
        assert session_client.get("/api/github-delivery/callback/", {"state": state, "code": "code"}).status_code == 302
    connection = GitHubConnection.objects.get(installation_id=456, host="github.com")
    # Workspace-wide management stays workspace-admin-only.
    assert session_client.delete(root(board) + f"connections/{connection.id}/").status_code == 403
    # Losing the project administrator role closes connection and reads.
    ProjectMember.objects.filter(project=board.project, member=board.user).update(role=5)
    assert session_client.post(root(board) + "connect/", {"account_type": "personal"}, format="json").status_code == 403
    assert session_client.get(development(board)).status_code == 403
    assert session_client.get(issue_url(board)).status_code == 403


def test_signed_delivery_replay_scoped_linking_and_rollback(board, session_client, boundaries):
    delivery_id = uuid4()
    assert webhook(event(board), signature="sha256=forged").status_code == 403
    assert not GitHubWebhookDelivery.objects.exists()
    response = webhook(event(board), delivery_id)
    assert response.status_code == 202
    assert not GitHubPullRequest.objects.exists()  # request queues; worker owns projection
    services.process_delivery(delivery_id)
    pr = GitHubPullRequest.objects.get()
    assert services.list_issue_pull_requests(board.issue)[0]["id"] == str(pr.id)
    assert webhook(event(board), delivery_id).data["duplicate"] is True
    services.process_delivery(delivery_id)
    assert GitHubIssueLink.objects.count() == 1
    assert webhook(event(board, action="closed"), delivery_id).status_code == 409
    assert webhook({"installation": []}).status_code == 400
    assert webhook(event(board, repository={"id": 999})).status_code == 202
    unscoped = GitHubWebhookDelivery.objects.filter(status="queued").get()
    services.process_delivery(unscoped.id)
    assert GitHubPullRequest.objects.count() == 1
    before = GitHubWebhookDelivery.objects.count()
    with pytest.raises(RuntimeError):
        with transaction.atomic():
            webhook(event(board))
            raise RuntimeError("request rolls back")
    assert GitHubWebhookDelivery.objects.count() == before
    assert session_client.delete(issue_url(board) + f"{pr.id}/").status_code == 204
    newer = event(board, pull_request=pr_payload(board, updated_at="2026-09-25T13:00:00Z"))
    next_id = uuid4()
    webhook(newer, next_id)
    services.process_delivery(next_id)
    assert services.list_issue_pull_requests(board.issue) == []
    assert session_client.post(issue_url(board), {"pull_request_id": str(pr.id)}, format="json").status_code == 201
    assert services.list_issue_pull_requests(board.issue)[0]["linked_manually"] is True


def test_project_isolation_manual_url_and_release_sources(board, session_client):
    second = Project.objects.create(name="Other", identifier="OTHER", workspace=board.workspace)
    foreign_map = GitHubRepositoryMapping.objects.create(
        connection=board.connection, project=second, repository_id=800, full_name="team/other"
    )
    foreign = GitHubPullRequest.objects.create(mapping=foreign_map, github_id=801, number=1, title="Private")
    assert session_client.post(issue_url(board), {"pull_request_id": str(foreign.id)}, format="json").status_code == 404
    assert not services.validate_project_pull_requests(board.project, {str(foreign.id)})
    with pytest.raises(ValidationError):
        services.get_release_sources(board.project, [str(foreign.id)])
    with patch(
        "requests.request",
        side_effect=lambda method, url, **kw: (
            HTTP(pr_payload(board))
            if urlsplit(url).path == "/repos/team/repo/pulls/9"
            else github_http(method, url, **kw)
        ),
    ):
        response = session_client.post(issue_url(board), {"url": "https://github.com/team/repo/pull/9"}, format="json")
        assert response.status_code == 201, response.data
    pr = GitHubPullRequest.objects.get(mapping=board.mapping)
    assert session_client.post(issue_url(board), {"url": "https://attacker.test/team/repo/pull/9"}).status_code == 400
    assert session_client.post(issue_url(board), {"url": 7}, format="json").status_code == 400
    release = services.upsert_release(
        board.mapping, {"id": 1000, "tag_name": "v1", "body": "Private release", "published_at": "2026-09-25T15:00:00Z"}
    )
    sources = services.get_release_sources(board.project, [str(pr.id)], release.id)
    assert [item["text"] for item in sources] == ["Private evidence", "Private release"]
    assert session_client.delete(root(board) + f"connections/{board.connection.id}/").status_code == 204
    assert services.list_issue_pull_requests(board.issue)[0]["connected"] is False
    assert services.get_release_sources(board.project, [str(pr.id)], release.id) == sources
    assert session_client.get(development(board)).data["pull_requests"][0]["id"] == str(pr.id)
    assert session_client.post(issue_url(board), {"url": "https://github.com/team/repo/pull/9"}).status_code == 404


def test_release_review_events_stale_data_and_disconnection(board):
    delivery = uuid4()
    webhook(event(board), delivery)
    services.process_delivery(delivery)
    review_id = uuid4()
    webhook(
        event(board, action="submitted", review={"state": "approved", "submitted_at": "2026-09-25T14:00:00Z"}),
        review_id,
        "pull_request_review",
    )
    services.process_delivery(review_id)
    assert GitHubPullRequest.objects.get().review_state == "approved"
    release_id = uuid4()
    payload = event(
        board,
        action="published",
        release={"id": 1000, "tag_name": "v1", "body": "Notes", "published_at": "2026-09-25T15:00:00Z"},
    )
    webhook(payload, release_id, "release")
    services.process_delivery(release_id)
    assert services.list_project_releases(board.project)[0]["tag_name"] == "v1"
    stale = uuid4()
    webhook(event(board, pull_request=pr_payload(board, title="Old", updated_at="2026-09-24T12:00:00Z")), stale)
    services.process_delivery(stale)
    assert GitHubPullRequest.objects.get().title != "Old"
    removal = uuid4()
    webhook(event(board, action="removed", repositories_removed=[{"id": 789}]), removal, "installation_repositories")
    services.process_delivery(removal)
    board.mapping.refresh_from_db()
    assert not board.mapping.is_active
    after = uuid4()
    webhook(
        event(board, pull_request=pr_payload(board, title="Must not apply", updated_at="2026-09-26T12:00:00Z")), after
    )
    services.process_delivery(after)
    assert GitHubPullRequest.objects.get().title != "Must not apply"


def test_mapping_repository_authorization_and_initial_sync(board, session_client):
    with patch("requests.request", side_effect=github_http):
        data = {"connection_id": str(board.connection.id), "project_id": str(board.project.id), "repository_id": 999}
        assert session_client.post(root(board) + "mappings/", data, format="json").status_code == 403
        data["repository_id"] = 789
        assert session_client.post(root(board) + "mappings/", data, format="json").status_code == 200

    def sync_http(method, url, **kwargs):
        if urlsplit(url).path == "/repos/team/repo/pulls":
            return HTTP([pr_payload(board)])
        if urlsplit(url).path == "/repos/team/repo/releases":
            return HTTP([{"id": 1000, "tag_name": "v1"}])
        return github_http(method, url, **kwargs)

    with patch("requests.request", side_effect=sync_http):
        sync_github_mapping(str(board.mapping.id))
    board.mapping.refresh_from_db()
    assert board.mapping.sync_status == "synced"
    assert board.mapping.last_synced_at
    assert GitHubPullRequest.objects.count() == GitHubRelease.objects.count() == 1


def test_expired_nonce_and_malformed_event_are_inert(board, session_client):
    from datetime import timedelta
    from django.utils import timezone

    state = authorize_state(session_client, board)
    GitHubConnectNonce.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    with patch("requests.request") as http:
        assert session_client.get("/api/github-delivery/callback/", {"state": state, "code": "code"}).status_code == 404
        http.assert_not_called()
    delivery_id = uuid4()
    webhook(event(board, pull_request=pr_payload(board, base={"repo": {"id": 999}})), delivery_id)
    services.process_delivery(delivery_id)
    assert not GitHubPullRequest.objects.exists()
    assert GitHubWebhookDelivery.objects.get(id=delivery_id).status == "failed"
    with pytest.raises(ValidationError):
        services.get_release_sources(board.project, [], "not-a-uuid")
    candidate = services.upsert_pull_request(
        board.mapping, pr_payload(board, title=f"DEV-{board.issue.sequence_id}suffix", head={"ref": "none"})
    )
    assert not GitHubIssueLink.objects.filter(pull_request=candidate).exists()


def test_project_deletion_and_queue_isolation(board):
    from django.db.models.deletion import Collector
    from plane.app.github_delivery.tasks import process_github_delivery, recover_pending_github_deliveries

    services.upsert_pull_request(board.mapping, pr_payload(board))
    services.upsert_release(board.mapping, {"id": 1000, "tag_name": "v1"})
    collector = Collector(using="default")
    collector.collect([board.project])
    assert board.project in collector.data[Project]
    assert (
        process_github_delivery.queue
        == sync_github_mapping.queue
        == recover_pending_github_deliveries.queue
        == "github-delivery"
    )


def test_webhook_before_callback_and_mapping_recovers_review(board, session_client):
    from plane.app.github_delivery.tasks import recover_pending_github_deliveries

    board.mapping.delete()
    board.connection.delete()
    delivery_id = uuid4()
    payload = event(board, action="submitted", review={"state": "approved", "submitted_at": "2026-09-25T14:00:00Z"})
    assert webhook(payload, delivery_id, "pull_request_review").data["status"] == "waiting"
    services.process_delivery(delivery_id)
    delivery = GitHubWebhookDelivery.objects.get(id=delivery_id)
    assert delivery.connection_id is None and delivery.payload == payload
    state = authorize_state(session_client, board)
    with patch("requests.request", side_effect=github_http):
        assert session_client.get("/api/github-delivery/callback/", {"state": state, "code": "code"}).status_code == 302
        board.connection = GitHubConnection.objects.get(installation_id=456, host="github.com")
        services.process_delivery(delivery_id)
        delivery.refresh_from_db()
        assert delivery.status == "awaiting_mapping" and delivery.connection_id == board.connection.id
        assert not GitHubPullRequest.objects.exists()
        assert (
            session_client.post(
                root(board) + "mappings/",
                {
                    "connection_id": str(board.connection.id),
                    "project_id": str(board.project.id),
                    "repository_id": 789,
                },
                format="json",
            ).status_code
            == 201
        )
    with patch("plane.app.github_delivery.tasks.process_github_delivery.delay") as queue:
        recover_pending_github_deliveries()
        queue.assert_called_once_with(str(delivery_id))
    assert webhook(payload, delivery_id, "pull_request_review").data["duplicate"] is True
    services.process_delivery(delivery_id)
    assert GitHubPullRequest.objects.get().review_state == "approved"
    assert GitHubIssueLink.objects.count() == 1
    delivery.refresh_from_db()
    assert delivery.status == "processed" and delivery.payload == {}
    services.process_delivery(delivery_id)
    assert GitHubPullRequest.objects.count() == 1


def test_waiting_events_expire_and_never_rebind_a_deleted_connection(board):
    from datetime import timedelta
    from django.utils import timezone
    from plane.db.models import Workspace
    from plane.app.github_delivery.tasks import recover_pending_github_deliveries

    board.mapping.delete()
    pending_id = uuid4()
    webhook(event(board), pending_id)
    # An already-accepted delivery from before migration 0164 has null lookup fields.
    GitHubWebhookDelivery.objects.filter(id=pending_id).update(installation_id=None, repository_id=None)
    services.process_delivery(pending_id)
    delivery = GitHubWebhookDelivery.objects.get(id=pending_id)
    assert delivery.status == "awaiting_mapping"
    board.connection.delete()
    other = Workspace.objects.create(name="Other", slug="other-workspace", owner=board.user)
    GitHubConnection.objects.create(
        workspace=other,
        app=board.app,
        host="github.com",
        installation_id=456,
        account_login="team",
        github_user_id=7,
        connected_by=board.user,
    )
    services.process_delivery(pending_id)
    delivery.refresh_from_db()
    assert delivery.status == "ignored" and delivery.connection_id is None and delivery.payload == {}
    unknown = event(board, installation={"id": 999})
    expired_id = uuid4()
    webhook(unknown, expired_id)
    GitHubWebhookDelivery.objects.filter(id=expired_id).update(received_at=timezone.now() - timedelta(hours=25))
    with patch("plane.app.github_delivery.tasks.process_github_delivery.delay") as queue:
        recover_pending_github_deliveries()
        queue.assert_not_called()
    expired = GitHubWebhookDelivery.objects.get(id=expired_id)
    assert expired.status == "ignored" and expired.error == "Expired" and expired.payload == {}
    assert (
        webhook(event(board, installation={"id": 888}, action="created"), event_name="installation").data["status"]
        == "ignored"
    )
    assert not GitHubWebhookDelivery.objects.filter(installation_id=888).exists()
    before = GitHubWebhookDelivery.objects.count()
    assert webhook(event(board, oversized="x" * 1048576)).status_code == 413
    assert GitHubWebhookDelivery.objects.count() == before
    GitHubWebhookDelivery.objects.bulk_create(
        [
            GitHubWebhookDelivery(
                id=uuid4(),
                host="github.com",
                installation_id=777,
                repository_id=789,
                event="pull_request",
                body_hash="0" * 64,
                status="waiting",
                payload={},
            )
            for _ in range(200)
        ]
    )
    assert webhook(event(board, installation={"id": 777})).status_code == 503
    assert GitHubWebhookDelivery.objects.filter(installation_id=777).count() == 200


def test_transient_projection_failure_retries_atomically_with_attempt_limit(board):
    from datetime import timedelta
    from django.db import OperationalError
    from django.utils import timezone
    from plane.app.github_delivery.tasks import recover_pending_github_deliveries

    delivery_id = uuid4()
    webhook(event(board), delivery_id)
    with patch.object(
        GitHubIssueLink.objects, "get_or_create", side_effect=OperationalError("temporary storage failure")
    ):
        services.process_delivery(delivery_id)
    delivery = GitHubWebhookDelivery.objects.get(id=delivery_id)
    assert delivery.status == "retry" and delivery.processing_attempts == 1
    assert delivery.payload and delivery.next_retry_at > timezone.now()
    assert not GitHubPullRequest.objects.exists()  # PR insert preceding link failure rolled back
    services.process_delivery(delivery_id)  # early duplicate does not consume an attempt
    delivery.refresh_from_db()
    assert delivery.processing_attempts == 1
    GitHubWebhookDelivery.objects.filter(id=delivery_id).update(
        received_at=timezone.now() - timedelta(minutes=2),
        next_retry_at=timezone.now() - timedelta(seconds=1),
    )
    with patch("plane.app.github_delivery.tasks.process_github_delivery.delay") as queue:
        recover_pending_github_deliveries()
        queue.assert_called_once_with(str(delivery_id))
    services.process_delivery(delivery_id)
    delivery.refresh_from_db()
    assert delivery.status == "processed" and delivery.processing_attempts == 2
    assert GitHubPullRequest.objects.count() == GitHubIssueLink.objects.count() == 1
    exhausted_id = uuid4()
    webhook(event(board), exhausted_id)
    with patch.object(
        GitHubIssueLink.objects, "get_or_create", side_effect=OperationalError("temporary storage failure")
    ):
        for attempt in range(5):
            GitHubWebhookDelivery.objects.filter(id=exhausted_id).update(next_retry_at=None)
            services.process_delivery(exhausted_id)
    exhausted = GitHubWebhookDelivery.objects.get(id=exhausted_id)
    assert exhausted.status == "failed" and exhausted.processing_attempts == 5 and exhausted.payload == {}
    services.process_delivery(exhausted_id)
    exhausted.refresh_from_db()
    assert exhausted.processing_attempts == 5


def test_webhook_signature_selects_the_right_app(board):
    """A delivery signed by another stored App's secret never reaches this App's installation."""
    other = GitHubApp.objects.create(
        host="github.com",
        app_id=321,
        slug="other-app",
        client_id="Iv.other",
        client_secret=encrypt_secret("other-secret"),
        private_key=encrypt_secret(boundaries.pem),
        webhook_secret=encrypt_secret("other-webhook-secret"),
    )
    delivery_id = uuid4()
    raw = json.dumps(event(board)).encode()
    forged = "sha256=" + hmac.new(b"other-webhook-secret", raw, hashlib.sha256).hexdigest()
    client = APIClient()
    response = client.post(
        "/api/github-delivery/webhooks/",
        raw,
        content_type="application/json",
        HTTP_X_HUB_SIGNATURE_256=forged,
        HTTP_X_GITHUB_DELIVERY=str(delivery_id),
        HTTP_X_GITHUB_EVENT="pull_request",
    )
    # Signed by a real App but for a foreign installation: retained inertly, never projected.
    assert response.status_code == 202 and response.data["status"] == "waiting"
    services.process_delivery(delivery_id)
    assert not GitHubPullRequest.objects.exists()
    forged_delivery = GitHubWebhookDelivery.objects.get(id=delivery_id)
    assert forged_delivery.status == "awaiting_mapping" or forged_delivery.status == "waiting"
    # The correctly signed delivery projects normally, even with the foreign delivery retained.
    assert webhook(event(board), uuid4()).status_code == 202
    services.process_delivery(GitHubWebhookDelivery.objects.exclude(id=delivery_id).get().id)
    assert GitHubPullRequest.objects.count() == 1
    other.delete()
