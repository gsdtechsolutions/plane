# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
from django.contrib.auth import get_user_model

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from plane.db.models import Workspace, WorkspaceMember
from plane.license.models import InstanceConfiguration
from plane.app.ai_ops.configuration import KEY_API_KEY, KEY_MODEL, KEY_PROVIDER

User = get_user_model()

pytestmark = [pytest.mark.contract, pytest.mark.django_db(transaction=True)]

URL = "/api/workspaces/{slug}/ai-configuration/"
SECRET = "sk-litellm-test-key-9abc"


@pytest.fixture
def setup(create_user):
    workspace = Workspace.objects.create(name="Cfg", slug="cfg-ws", owner=create_user)
    WorkspaceMember.objects.create(workspace=workspace, member=create_user, role=20, is_active=True)
    other_admin = User.objects.create(email="admin2@cfg.test", username="admin2", password="x")
    WorkspaceMember.objects.create(workspace=workspace, member=other_admin, role=20, is_active=True)
    member = User.objects.create(email="member@cfg.test", username="member", password="x")
    WorkspaceMember.objects.create(workspace=workspace, member=member, role=5, is_active=True)
    return workspace, create_user, other_admin, member


def _login(client, user):
    client.force_authenticate(user=user)


def test_get_requires_owner(setup):
    workspace, owner, other_admin, member = setup
    client = APIClient()
    client.force_authenticate(user=other_admin)
    assert client.get(URL.format(slug=workspace.slug)).status_code == 403
    client.force_authenticate(user=member)
    assert client.get(URL.format(slug=workspace.slug)).status_code == 403
    client.force_authenticate(user=None)
    assert client.get(URL.format(slug=workspace.slug)).status_code in (401, 403)
    client.force_authenticate(user=owner)
    assert client.get(URL.format(slug=workspace.slug)).status_code == 200


def test_get_masks_key_and_reports_configured(setup):
    workspace, owner, *_ = setup
    InstanceConfiguration.objects.create(
        key=KEY_API_KEY, value=SECRET, category="ALL", is_encrypted=False, created_at=timezone.now(),
        updated_at=timezone.now(),
    )
    InstanceConfiguration.objects.create(
        key=KEY_MODEL, value="glm-5.3-flash", category="ALL", is_encrypted=False, created_at=timezone.now(),
        updated_at=timezone.now(),
    )
    client = APIClient()
    client.force_authenticate(user=owner)
    data = client.get(URL.format(slug=workspace.slug)).data
    assert data["llm_api_key_masked"].endswith(SECRET[-4:])
    assert SECRET not in (data["llm_api_key_masked"] or "")
    assert data["llm_model"] == "glm-5.3-flash"
    assert data["configured"] is True


def test_post_upserts_and_encrypts_key_at_rest(setup):
    workspace, owner, *_ = setup
    client = APIClient()
    client.force_authenticate(user=owner)
    with _patch_verify():
        response = client.post(
            URL.format(slug=workspace.slug),
            {"llm_provider": "openai", "llm_model": "test-model", "llm_api_key": SECRET, "test": False},
            format="json",
        )
    assert response.status_code == 200
    assert response.data["saved"] is True
    row = InstanceConfiguration.objects.filter(key=KEY_API_KEY).first()
    assert row.is_encrypted is True
    assert SECRET not in (row.value or "")  # encrypted at rest
    assert InstanceConfiguration.objects.filter(key=KEY_PROVIDER).first().value == "openai"
    # masked GET reflects the saved key
    client.force_authenticate(user=owner)
    assert client.get(URL.format(slug=workspace.slug)).data["llm_api_key_masked"].endswith("9abc")


def test_post_blank_key_keeps_existing_and_clears_base_url(setup):
    workspace, owner, *_ = setup
    InstanceConfiguration.objects.create(
        key=KEY_API_KEY, value="keep-me-encrypted", category="ALL", is_encrypted=True, created_at=timezone.now(),
        updated_at=timezone.now(),
    )
    client = APIClient()
    client.force_authenticate(user=owner)
    response = client.post(
        URL.format(slug=workspace.slug),
        {"llm_provider": "anthropic", "llm_model": "m1", "llm_base_url": "", "test": False},
        format="json",
    )
    assert response.status_code == 200
    assert InstanceConfiguration.objects.filter(key=KEY_API_KEY).first().value == "keep-me-encrypted"
    assert InstanceConfiguration.objects.filter(key="LLM_BASE_URL").exists() is False


def test_post_rejects_unknown_provider(setup):
    workspace, owner, *_ = setup
    client = APIClient()
    client.force_authenticate(user=owner)
    response = client.post(
        URL.format(slug=workspace.slug), {"llm_provider": "skynet", "llm_model": "m"}, format="json"
    )
    assert response.status_code == 400


def test_post_for_non_owner_forbidden(setup):
    workspace, owner, other_admin, member = setup
    client = APIClient()
    for user in (other_admin, member):
        client.force_authenticate(user=user)
        response = client.post(
            URL.format(slug=workspace.slug), {"llm_provider": "openai", "llm_model": "m"}, format="json"
        )
        assert response.status_code == 403
    assert InstanceConfiguration.objects.filter(key=KEY_MODEL).exists() is False


def test_post_runs_live_test_and_audits(setup):
    workspace, owner, *_ = setup
    client = APIClient()
    client.force_authenticate(user=owner)
    with _patch_verify(ok=True):
        response = client.post(
            URL.format(slug=workspace.slug),
            {"llm_provider": "openai", "llm_model": "test-model", "llm_api_key": SECRET, "test": True},
            format="json",
        )
    assert response.data["tested"] is True and response.data["test_ok"] is True

    with _patch_verify(ok=False):
        response = client.post(
            URL.format(slug=workspace.slug),
            {"llm_provider": "openai", "llm_model": "test-model", "llm_api_key": SECRET, "test": True},
            format="json",
        )
    assert response.data["test_ok"] is False and "test_error" in response.data

    from plane.db.models.ai_audit import AIActionAudit

    assert AIActionAudit.objects.filter(action="instance.ai_configure").count() == 2
    assert AIActionAudit.objects.filter(action="instance.ai_configure", status="error").count() == 1


class _patch_verify:
    """Patch provider.verify_provider in the configuration module namespace."""

    def __init__(self, ok=True):
        self.ok = ok

    def __enter__(self):
        from unittest.mock import patch

        def _ok():
            return {"ok": True, "model": "test-model"}

        def _bad():
            from plane.app.release_intelligence.provider import IntelligenceError

            raise IntelligenceError("401 invalid key")

        self._p = patch(
            "plane.app.ai_ops.configuration.provider.verify_provider", _ok if self.ok else _bad
        )
        self._p.__enter__()
        return self

    def __exit__(self, *args):
        self._p.__exit__(*args)
