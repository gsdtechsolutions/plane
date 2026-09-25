# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Unit tests for the infra proxy layer: SSRF/host guard, error mapping,
and the Coolify/Grafana response normalizers. No DB, no network — outbound
HTTP is monkeypatched at the pinned_fetch boundary."""

import pytest
import requests

from plane.app.infra import client as infra_client
from plane.app.infra import coolify, grafana
from plane.app.infra.client import InfraConfigError, InfraUnavailable, assert_host_allowed


def _response(status_code=200, payload=None, text=""):
    class FakeResponse:
        def __init__(self):
            self.status_code = status_code

        def json(self):
            if payload is None:
                raise ValueError("no json")
            return payload

    return FakeResponse()


class FakeConnection:
    """Duck-typed stand-in for InfraConnection (no DB)."""

    def __init__(self, base_url="http://10.69.42.110:8000", service="coolify", token="tok"):
        self.base_url = base_url
        self.service = service
        self._token = token

    def get_api_token(self):
        return self._token


@pytest.mark.unit
class TestHostGuard:
    def test_link_local_rejected(self):
        with pytest.raises(InfraConfigError):
            assert_host_allowed("http://169.254.169.254/latest/meta-data")

    def test_ipv6_link_local_rejected(self):
        with pytest.raises(InfraConfigError):
            assert_host_allowed("http://[fe80::1]:8000")

    def test_private_ip_allowed(self):
        assert_host_allowed("http://10.69.42.110:8000") is None

    def test_hostname_allowed(self):
        assert_host_allowed("https://coolify.internal.example") is None

    def test_missing_hostname_rejected(self):
        with pytest.raises(InfraConfigError):
            assert_host_allowed("not a url")


@pytest.mark.unit
class TestRequestErrorMapping:
    def test_401_maps_to_infra_unavailable(self, monkeypatch):
        monkeypatch.setattr(infra_client, "pinned_fetch", lambda *a, **k: _response(401))
        with pytest.raises(InfraUnavailable, match="token"):
            infra_client.infra_request_json("GET", "http://10.0.0.5/api")

    def test_500_maps_to_infra_unavailable(self, monkeypatch):
        monkeypatch.setattr(infra_client, "pinned_fetch", lambda *a, **k: _response(500))
        with pytest.raises(InfraUnavailable):
            infra_client.infra_request_json("GET", "http://10.0.0.5/api")

    def test_transport_error_maps_to_infra_unavailable(self, monkeypatch):
        def boom(*a, **k):
            raise requests.ConnectionError("refused")

        monkeypatch.setattr(infra_client, "pinned_fetch", boom)
        with pytest.raises(InfraUnavailable):
            infra_client.infra_request_json("GET", "http://10.0.0.5/api")

    def test_non_json_maps_to_infra_unavailable(self, monkeypatch):
        monkeypatch.setattr(infra_client, "pinned_fetch", lambda *a, **k: _response(200, payload=None))
        with pytest.raises(InfraUnavailable):
            infra_client.infra_request_json("GET", "http://10.0.0.5/api")

    def test_token_sent_as_bearer_header(self, monkeypatch):
        seen = {}

        def fake_fetch(method, url, *, allowed_hosts=None, headers=None, timeout=30, **kwargs):
            seen["headers"] = headers
            seen["url"] = url
            return _response(200, payload={"ok": True})

        monkeypatch.setattr(infra_client, "pinned_fetch", fake_fetch)
        infra_client.infra_request_json("GET", "http://10.0.0.5/api/v1/healthcheck", api_token="sekret")
        assert seen["headers"]["Authorization"] == "Bearer sekret"
        # The token must travel only in the header, never in the URL.
        assert "sekret" not in seen["url"]


@pytest.mark.unit
class TestCoolifyNormalizer:
    def test_docker_app_version_from_image_tag(self):
        app = {
            "uuid": "u1",
            "status": "running",
            "docker_registry_image_name": "ghcr.io/acme/api:1.4.2",
            "health_check_enabled": True,
            "health_check_path": "/health",
            "health_check_port": 8000,
            "health_check_period": 30,
            "fqdn": "api.example.com,alt.example.com",
        }
        status = coolify.normalize_app_status(app, [], base_url="http://coolify:8000")
        assert status["version"]["label"] == "1.4.2"
        assert status["version"]["image_tag"] == "1.4.2"
        assert status["health"] == {"enabled": True, "path": "/health", "port": 8000, "check_period": 30}
        assert status["status"] == "running"
        assert status["app_url"] == "api.example.com"

    def test_git_app_version_from_latest_finished_deployment(self):
        deployments = [
            {"deployment_uuid": "d2", "status": "in_progress", "commit": "ffffff", "commit_message": "wip"},
            {"deployment_uuid": "d1", "status": "finished", "commit": "abc1234def", "commit_message": "fix: bug", "finished_at": "2026-09-25T00:00:00Z"},
        ]
        status = coolify.normalize_app_status({"uuid": "u1"}, deployments, base_url="")
        assert status["version"]["commit_sha"] == "abc1234def"
        assert status["version"]["label"] == "abc1234"
        assert status["version"]["message"] == "fix: bug"
        assert status["version"]["deployed_at"] == "2026-09-25T00:00:00Z"
        assert len(status["deployments"]) == 2

    def test_missing_fields_degrade_to_none(self):
        status = coolify.normalize_app_status({}, [], base_url="")
        assert status["version"]["label"] is None
        assert status["status"] is None
        assert status["health"]["enabled"] is False
        assert status["deployments"] == []
        assert status["deep_link"] is None

    def test_deployments_capped_at_five(self):
        many = [{"deployment_uuid": f"d{i}", "status": "finished"} for i in range(12)]
        status = coolify.normalize_app_status({}, many, base_url="")
        assert len(status["deployments"]) == 5

    def test_deep_link_built_from_environment(self):
        app = {"uuid": "u1", "environment": {"project_id": "p1", "name": "production"}}
        status = coolify.normalize_app_status(app, [], base_url="http://coolify:8000/")
        assert status["deep_link"] == "http://coolify:8000/project/p1/production/application/u1"

    def test_verify_surfaces_error_as_data(self):
        conn = FakeConnection()

        def boom(connection, path, timeout=8):
            raise InfraUnavailable("External service returned HTTP 401.")

        monkey = pytest.MonkeyPatch()
        monkey.setattr(coolify, "_get", boom)
        result = coolify.verify(conn)
        monkey.undo()
        assert result["ok"] is False
        assert "401" in result["error"]


@pytest.mark.unit
class TestGrafanaAdapter:
    def test_dashboard_normalization_and_absolute_url(self, monkeypatch):
        monkeypatch.setattr(
            grafana,
            "_get",
            lambda connection, path, timeout=10: [
                {"uid": "rY4d", "title": "Overview", "url": "/d/rY4d/overview", "folderTitle": "Prod", "tags": ["a"]},
                {"uid": None, "title": "skipped"},
            ],
        )
        conn = FakeConnection(base_url="https://grafana.example.com", service="grafana")
        dashboards = grafana.list_dashboards(conn)
        assert len(dashboards) == 1
        assert dashboards[0]["id"] == "rY4d"
        assert grafana.dashboard_absolute_url(conn, dashboards[0]) == "https://grafana.example.com/d/rY4d/overview"

    def test_absolute_url_falls_back_to_uid(self):
        conn = FakeConnection(base_url="https://grafana.example.com", service="grafana")
        assert grafana.dashboard_absolute_url(conn, {"id": "xY9z", "url": ""}) == "https://grafana.example.com/d/xY9z"

    def test_verify_reports_auth_failure(self, monkeypatch):
        def boom(connection, path, timeout=8):
            raise InfraUnavailable("External service rejected the configured API token.")

        monkey = pytest.MonkeyPatch()
        monkey.setattr(grafana, "_get", boom)
        result = grafana.verify(FakeConnection())
        monkey.undo()
        assert result["ok"] is False
        assert "token" in result["error"]
