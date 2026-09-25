# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
import os
from celery import shared_task

QUEUE = os.environ.get("GITHUB_DELIVERY_QUEUE", "github-delivery")


@shared_task(queue=QUEUE, name="github_delivery.process", autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
def process_github_delivery(delivery_id):
    from .services import process_delivery

    process_delivery(delivery_id)


@shared_task(queue=QUEUE, name="github_delivery.sync_mapping")
def sync_github_mapping(mapping_id):
    from plane.db.models.github_delivery import GitHubRepositoryMapping
    from .services import sync_mapping

    try:
        sync_mapping(mapping_id)
    except Exception:
        GitHubRepositoryMapping.objects.filter(id=mapping_id, is_active=True).update(
            sync_status="failed", sync_error="GitHub could not sync this repository. Check the connection and retry."
        )
        raise


@shared_task(queue=QUEUE, name="github_delivery.recover_pending")
def recover_pending_github_deliveries():
    """Recover committed deliveries whose initial broker publication failed."""
    from datetime import timedelta
    from django.utils import timezone
    from plane.db.models.github_delivery import GitHubWebhookDelivery, GitHubRepositoryMapping

    pending = (
        GitHubWebhookDelivery.objects.filter(
            status="queued",
            received_at__lt=timezone.now() - timedelta(minutes=1),
            connection__is_active=True,
        )
        .order_by("received_at")
        .values_list("id", flat=True)[:100]
    )
    for delivery_id in pending:
        process_github_delivery.delay(str(delivery_id))

    mappings = (
        GitHubRepositoryMapping.objects.filter(
            sync_status="pending",
            is_active=True,
            connection__is_active=True,
            created_at__lt=timezone.now() - timedelta(minutes=1),
        )
        .order_by("created_at")
        .values_list("id", flat=True)[:20]
    )
    for mapping_id in mappings:
        sync_github_mapping.delay(str(mapping_id))
