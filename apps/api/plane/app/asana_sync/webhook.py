# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Asana webhook receiver (public, unauthenticated by session).

Protocol: on webhook registration Asana POSTs once with `X-Hook-Secret` — the
receiver must answer 2xx echoing the same header back. Every later delivery
carries `X-Hook-Signature` (HMAC-SHA256 of the raw body with that secret) and
is verified before dispatch. Deliveries that verify enqueue a targeted pull
per task gid; the engine's link/loop guards make redelivery a no-op.
"""

# Python imports
import hashlib
import hmac
import json
import logging
import traceback

# Third party imports
from celery import shared_task
from rest_framework.permissions import AllowAny

# Django imports
from django.http import HttpResponse
from rest_framework import status
from rest_framework.views import APIView

# Module imports
from plane.app.asana_sync.engine import AsanaSyncEngine
from plane.app.asana_sync.client import AsanaClient
from plane.app.asana_sync.crypto import decrypt_token
from plane.db.models import AsanaProjectSync
from plane.utils.exception_logger import log_exception

logger = logging.getLogger(__name__)


@shared_task
def asana_webhook_process(sync_id: str, task_gids: list[str]):
    """Targeted pulls for webhook-announced tasks (bounded, retried by celery)."""
    sync = (
        AsanaProjectSync.objects.select_related("connection", "project")
        .filter(id=sync_id, is_active=True, connection__is_active=True, deleted_at__isnull=True)
        .first()
    )
    if sync is None:
        return "sync gone"
    try:
        token = decrypt_token(sync.connection.pat_encrypted)
    except Exception:
        return "token unavailable"
    client = AsanaClient(token)
    engine = AsanaSyncEngine(sync, client)
    processed = 0
    for task_gid in task_gids[:50]:
        try:
            if engine.pull_task_by_gid(task_gid):
                processed += 1
        except Exception:
            log_exception(traceback.format_exc())
    return processed


class AsanaWebhookEndpoint(APIView):
    """POST /api/asana-sync/webhook/<sync_id>/ — public (signature-verified), same
    mounting strategy as the github-delivery webhook endpoint."""

    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request, sync_id):
        sync = AsanaProjectSync.objects.filter(
            id=sync_id, is_active=True, deleted_at__isnull=True
        ).first()

        # Handshake: echo the secret back so Asana completes registration.
        hook_secret = request.headers.get("X-Hook-Secret")
        if hook_secret:
            if sync is None:
                return HttpResponse(status=404)
            sync.webhook_secret = hook_secret
            sync.webhook_configured = True
            sync.save(update_fields=["webhook_secret", "webhook_configured", "updated_at"])
            response = HttpResponse(status=status.HTTP_200_OK)
            response["X-Hook-Secret"] = hook_secret
            return response

        if sync is None or not sync.webhook_secret:
            # Answer 200 anyway so Asana does not disable the webhook for other syncs'
            # transient failures; unverifiable deliveries are dropped, not acted on.
            return HttpResponse(status=status.HTTP_200_OK)

        signature = request.headers.get("X-Hook-Signature", "")
        expected = hmac.new(
            sync.webhook_secret.encode("utf-8"), request.body, hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(signature, expected):
            logger.warning("Asana webhook signature mismatch for sync %s", sync_id)
            return HttpResponse(status=status.HTTP_200_OK)

        try:
            payload = json.loads(request.body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return HttpResponse(status=status.HTTP_200_OK)

        task_gids = []
        for event in payload.get("events") or []:
            if event.get("type") != "task":
                continue
            resource = event.get("resource") or {}
            gid = resource.get("gid")
            if gid:
                task_gids.append(gid)

        if task_gids:
            asana_webhook_process.delay(str(sync_id), task_gids)
        return HttpResponse(status=status.HTTP_200_OK)
