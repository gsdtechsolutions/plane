# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Celery task wrapper for the AFFiNE wiki sync engine.

The REST endpoint triggers syncs synchronously for small page counts but
enqueues this task when the mapping table is large, so a slow AFFiNE round
trip can never hold a request worker for minutes. Also importable by a
periodic beat schedule if the coordinator wires one later.
"""

import logging

from celery import shared_task
from celery.utils.log import get_task_logger

logger = get_task_logger(__name__)
stdout_logger = logging.getLogger(__name__)


def _run(connection_id):
    # imports deferred so this module stays import-safe pre-django-setup
    from plane.affine_sync.engine import run_sync
    from plane.db.models import AffineConnection

    connection = AffineConnection.objects.filter(pk=connection_id, is_active=True).first()
    if connection is None:
        logger.info("affine_sync_skipped connection=%s missing_or_inactive", connection_id)
        return {"status": "skipped", "reason": "missing_or_inactive"}
    try:
        stats = run_sync(connection)
    except Exception as exc:
        # engine.run_sync already records per-page errors; this catches
        # unexpected crashes so celery retries don't pile up silently
        logger.exception("affine_sync_crashed connection=%s", connection_id)
        return {"status": "error", "reason": str(exc)}
    logger.info("affine_sync_done connection=%s stats=%s", connection_id, stats)
    return {"status": "ok", "stats": stats}


@shared_task
def affine_sync_connection(connection_id):
    """Sync one AFFiNE connection by primary key."""
    return _run(connection_id)


@shared_task
def affine_sync_all_active():
    """Fan out one sync task per active connection (for scheduled runs)."""
    from plane.db.models import AffineConnection

    ids = list(
        AffineConnection.objects.filter(is_active=True, deleted_at__isnull=True).values_list("id", flat=True)
    )
    for connection_id in ids:
        affine_sync_connection.delay(str(connection_id))
    return {"scheduled": len(ids)}
