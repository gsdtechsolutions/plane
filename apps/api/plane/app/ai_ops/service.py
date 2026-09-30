# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Shared AI operations helpers for every fork AI feature.

All AI actions call log_ai_action() best-effort: auditing must never break the
action it records. Callers pass plain values (instances or ids are both fine);
this module does the shaping so feature code stays one-line simple."""

import logging

from plane.db.models.ai_audit import AIActionAudit
from plane.utils.exception_logger import log_exception

logger = logging.getLogger(__name__)

EXCERPT_LIMIT = 8000


def _excerpt(value, limit=EXCERPT_LIMIT):
    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    return text[:limit]


def _pk(value):
    if value is None:
        return ""
    return str(getattr(value, "pk", value))


def log_ai_action(
    *,
    workspace,
    action,
    project=None,
    actor=None,
    entity_type="",
    entity_id=None,
    model="",
    status=AIActionAudit.Status.SUCCESS,
    input_excerpt="",
    output_excerpt="",
    error="",
    latency_ms=None,
    metadata=None,
):
    """Record an AI action in the audit trail. Returns the row or None.

    status may be AIActionAudit.Status.SUCCESS/ERROR or either literal string.
    """
    try:
        return AIActionAudit.objects.create(
            workspace=workspace,
            project=project,
            actor=actor,
            action=action,
            entity_type=entity_type or "",
            entity_id=_pk(entity_id),
            model=model or "",
            status=status,
            input_excerpt=_excerpt(input_excerpt),
            output_excerpt=_excerpt(output_excerpt),
            error=_excerpt(error),
            latency_ms=latency_ms,
            metadata=metadata or {},
        )
    except Exception as exc:
        log_exception(exc)
        logger.warning("ai_ops: failed to record audit row for action=%s", action)
        return None
