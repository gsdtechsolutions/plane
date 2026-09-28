# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Celery tasks for the Asana sync: beat tick, per-sync pass, targeted push,
log cleanup."""

# Python imports
import logging
import traceback

# Third party imports
from celery import shared_task
from django.db.models import Q

# Django imports
from django.utils import timezone

# Module imports
from plane.app.asana_sync.engine import run_sync_pass
from plane.db.models import AsanaProjectSync, AsanaSyncLog, Issue
from plane.utils.exception_logger import log_exception

logger = logging.getLogger(__name__)

# A sync is "due" when last_synced_at is older than this (or never synced).
SYNC_INTERVAL_MINUTES = 5
LOG_RETENTION_DAYS = 30


@shared_task
def asana_sync_tick():
    """Beat entry: dispatch engine passes for every due, active sync."""
    threshold = timezone.now() - timezone.timedelta(minutes=SYNC_INTERVAL_MINUTES)
    due_syncs = AsanaProjectSync.objects.filter(
        is_active=True,
        connection__is_active=True,
        connection__deleted_at__isnull=True,
        deleted_at__isnull=True,
    ).filter(
        Q(last_synced_at__isnull=True) | Q(last_synced_at__lt=threshold)
    ).values_list("id", flat=True)[:100]

    dispatched = 0
    for sync_id in due_syncs:
        asana_sync_run.delay(str(sync_id))
        dispatched += 1
    return dispatched


@shared_task(
    autoretry_for=(Exception,),
    retry_kwargs={"max_retries": 3, "countdown": 60},
    retry_backoff=True,
)
def asana_sync_run(sync_id: str):
    """One engine pass over one sync (pull and/or push). Clears the manual-run lock."""
    from plane.settings.redis import redis_instance

    try:
        result = run_sync_pass(sync_id)
        if result.get("error"):
            logger.warning("Asana sync %s finished with error: %s", sync_id, result["error"])
        return result
    except Exception as exc:
        log_exception(traceback.format_exc())
        raise
    finally:
        try:
            redis_instance().delete(f"asana_sync_lock:{sync_id}")
        except Exception:
            logger.warning("Could not clear asana sync lock for %s", sync_id, exc_info=True)


@shared_task
def asana_sync_cleanup_old_logs():
    """Daily beat entry: prune sync logs older than the retention window."""
    cutoff = timezone.now() - timezone.timedelta(days=LOG_RETENTION_DAYS)
    deleted, _ = AsanaSyncLog.objects.filter(created_at__lt=cutoff).delete()
    return deleted


@shared_task(
    autoretry_for=(Exception,),
    retry_kwargs={"max_retries": 2, "countdown": 30},
    retry_backoff=True,
)
def asana_sync_push_issue(sync_id: str, issue_id: str):
    """Signal-triggered near-real-time push of one issue to one sync.

    Takes the shared sync lock only when free — a running full pass wins and
    the beat tick catches the change up within SYNC_INTERVAL_MINUTES.
    """
    from plane.app.asana_sync.client import AsanaClient
    from plane.app.asana_sync.crypto import AsanaCryptoError, decrypt_token
    from plane.app.asana_sync.engine import AsanaSyncEngine
    from plane.settings.redis import redis_instance

    lock_key = f"asana_sync_lock:{sync_id}"
    if not redis_instance().set(lock_key, "1", nx=True, ex=120):
        return "busy"
    try:
        sync = (
            AsanaProjectSync.objects.select_related("connection", "project")
            .filter(id=sync_id, is_active=True, connection__is_active=True, deleted_at__isnull=True)
            .first()
        )
        if sync is None:
            return "sync gone"
        if sync.direction == "pull":
            return "pull-only"
        issue = Issue.objects.filter(id=issue_id, project_id=sync.project_id).first()
        if issue is None:
            return "issue gone"
        try:
            token = decrypt_token(sync.connection.pat_encrypted)
        except AsanaCryptoError:
            return "token unavailable"
        engine = AsanaSyncEngine(sync, AsanaClient(token))
        return engine.push_issue(issue)
    except Exception as exc:
        log_exception(traceback.format_exc())
        raise
    finally:
        try:
            redis_instance().delete(lock_key)
        except Exception:
            logger.warning("Could not clear asana sync lock for %s", sync_id, exc_info=True)
