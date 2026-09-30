# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Instance AI configuration — owner-only write surface for the LLM settings.

Values live in InstanceConfiguration (read by plane.license.utils.instance_value
get_configuration_value, which decrypts is_encrypted rows). Writes take effect
immediately — no restart — because SKIP_ENV_VAR defaults on and every
provider_config() call re-reads the table."""

from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response

from plane.app.views.base import BaseAPIView
from plane.db.models import Workspace
from plane.db.models.ai_audit import AIActionAudit
from plane.license.models import InstanceConfiguration
from plane.license.utils.encryption import decrypt_data, encrypt_data
from plane.app.ai_ops.service import log_ai_action
from plane.app.release_intelligence import provider

ALLOWED_PROVIDERS = ("openai", "gemini", "anthropic")
KEY_PROVIDER = "LLM_PROVIDER"
KEY_MODEL = "LLM_MODEL"
KEY_BASE_URL = "LLM_BASE_URL"
KEY_API_KEY = "LLM_API_KEY"


def instance_owner(user, slug):
    """Only the workspace owner may read or write instance AI configuration."""
    workspace = get_object_or_404(Workspace, slug=slug)
    if not user.is_authenticated or workspace.owner_id != user.id:
        raise PermissionDenied("Only the workspace owner can manage the AI configuration.")
    return workspace


def _config_row(key):
    return InstanceConfiguration.objects.filter(key=key).first()


def _read_value(key):
    row = _config_row(key)
    if row is None or not row.value:
        return ""
    if row.is_encrypted:
        try:
            return decrypt_data(row.value) or ""
        except Exception:
            return ""
    return row.value


def _write_value(key, value, *, encrypted, user):
    row = _config_row(key)
    if row is None:
        InstanceConfiguration.objects.create(
            key=key,
            value=value,
            category="ALL",
            is_encrypted=encrypted,
            created_by_id=user.id,
            updated_by_id=user.id,
        )
    else:
        row.value = value
        row.is_encrypted = encrypted
        row.updated_by_id = user.id
        row.updated_at = timezone.now()
        row.save(update_fields=["value", "is_encrypted", "updated_by_id", "updated_at"])


def _mask(key_value):
    if not key_value:
        return ""
    return "••••" + key_value[-4:]


class AIOperationsConfigSerializer(serializers.Serializer):
    llm_provider = serializers.ChoiceField(choices=list(ALLOWED_PROVIDERS))
    llm_model = serializers.CharField(max_length=128)
    llm_base_url = serializers.CharField(max_length=500, required=False, allow_blank=True, default="")
    llm_api_key = serializers.CharField(required=False, allow_blank=True, default="")
    test = serializers.BooleanField(required=False, default=True)


class AIOperationsConfigEndpoint(BaseAPIView):
    """GET: masked current values. POST: owner-only upsert + optional live test."""

    def get(self, request, slug):
        workspace = instance_owner(request.user, slug)
        key_value = _read_value(KEY_API_KEY)
        provider_value = _read_value(KEY_PROVIDER).lower()
        model_value = _read_value(KEY_MODEL)
        return Response(
            {
                "llm_provider": provider_value if provider_value in ALLOWED_PROVIDERS else "",
                "llm_model": model_value,
                "llm_base_url": _read_value(KEY_BASE_URL),
                "llm_api_key_masked": _mask(key_value),
                "configured": bool(key_value and model_value),
            },
            status=status.HTTP_200_OK,
        )

    def post(self, request, slug):
        workspace = instance_owner(request.user, slug)
        serializer = AIOperationsConfigSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        _write_value(KEY_PROVIDER, data["llm_provider"], encrypted=False, user=request.user)
        _write_value(KEY_MODEL, data["llm_model"].strip(), encrypted=False, user=request.user)
        if data["llm_base_url"].strip():
            _write_value(KEY_BASE_URL, data["llm_base_url"].strip(), encrypted=False, user=request.user)
        else:
            InstanceConfiguration.objects.filter(key=KEY_BASE_URL).delete()
        if data["llm_api_key"].strip():
            _write_value(
                KEY_API_KEY,
                encrypt_data(data["llm_api_key"].strip()),
                encrypted=True,
                user=request.user,
            )

        payload = {"saved": True}
        if data["test"]:
            try:
                result = provider.verify_provider()
                payload.update({"tested": True, "test_ok": True, "test_model": result.get("model", "")})
            except Exception as exc:
                payload.update({"tested": True, "test_ok": False, "test_error": str(exc)[:300]})

        log_ai_action(
            workspace=workspace,
            actor=request.user,
            action="instance.ai_configure",
            output_excerpt="provider=%s model=%s tested=%s test_ok=%s"
            % (data["llm_provider"], data["llm_model"], payload.get("tested"), payload.get("test_ok")),
            metadata={"tested": payload.get("tested", False), "test_ok": payload.get("test_ok")},
            status=AIActionAudit.Status.SUCCESS if payload.get("test_ok", True) else AIActionAudit.Status.ERROR,
        )
        return Response(payload, status=status.HTTP_200_OK)
