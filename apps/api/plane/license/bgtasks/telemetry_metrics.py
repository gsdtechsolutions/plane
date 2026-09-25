# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""GSD fork: instance telemetry is fully disabled.

The upstream implementation pushed OTLP instance metrics to Plane-hosted
collectors. This self-hosted fork does not phone home; the task is kept as a
no-op so existing imports and schedules (if any) remain import-safe.
"""

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task
def push_instance_metrics():
    """No-op. Telemetry removed in the GSD fork."""
    logger.debug("Telemetry disabled in GSD fork; skipping metrics push")
    return
