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


@shared_task(queue=QUEUE, name="github_delivery.search_mentions")
def search_github_issue_mentions(issue_id):
    from .services import search_issue_mentions

    search_issue_mentions(issue_id)


@shared_task(queue=QUEUE, name="github_delivery.backfill_workspace")
def backfill_github_workspace(workspace_id):
    """Deep history sweep over every repository of the workspace's connections.

    A long, best-effort walk: repositories are assigned to the project they
    mention most and their collected history ingested. Per-repository failures
    are swallowed and recorded on the repository's mapping; the task itself
    never retries automatically.
    """
    from .services import backfill_workspace

    backfill_workspace(workspace_id)


@shared_task(queue=QUEUE, name="github_delivery.backfill_all_workspaces")
def backfill_all_github_workspaces():
    """Zero-touch sweep: every active connection's repositories get pulled.

    Enqueues one per-workspace deep sweep so repositories never need manual
    mapping — the sweep attaches each accessible repository to the project
    its history mentions most. A short cache lock keeps an overlapping
    schedule fire from stacking duplicate sweeps; the lock expires quickly
    so a sweep kicked by a fresh connection activation is never blocked.
    """
    from django.core.cache import cache

    from plane.db.models.github_delivery import GitHubConnection

    workspace_ids = (
        GitHubConnection.objects.filter(is_active=True)
        .values_list("workspace_id", flat=True)
        .distinct()
    )
    for workspace_id in workspace_ids:
        if cache.add(f"github_delivery:backfill:{workspace_id}", "1", timeout=600):
            backfill_github_workspace.delay(str(workspace_id))


@shared_task(queue=QUEUE, name="github_delivery.recover_pending")
def recover_pending_github_deliveries():
    """Recover committed deliveries whose initial broker publication failed."""
    from datetime import timedelta
    from django.utils import timezone
    from django.db.models import Q, Exists, OuterRef, F
    from plane.db.models.github_delivery import GitHubWebhookDelivery, GitHubRepositoryMapping

    from .services import DELIVERY_RETENTION

    now = timezone.now()
    # Bound retention independently of whether an installation ever connects.
    GitHubWebhookDelivery.objects.filter(
        received_at__lt=now - DELIVERY_RETENTION,
        status__in=["waiting", "awaiting_mapping", "queued", "retry"],
    ).update(status="ignored", error="Expired", payload={}, next_retry_at=None, processed_at=now)
    pending = (
        GitHubWebhookDelivery.objects.filter(
            Q(next_retry_at__isnull=True) | Q(next_retry_at__lte=now),
            status__in=["queued", "retry"],
            received_at__lt=now - timedelta(minutes=1),
        )
        .order_by("received_at")
        .values_list("id", flat=True)[:100]
    )
    eligible_mapping = GitHubRepositoryMapping.objects.filter(
        is_active=True,
        connection__is_active=True,
        connection__app_id=OuterRef("app_id"),
        connection__host=OuterRef("host"),
        connection__installation_id=OuterRef("installation_id"),
        repository_id=OuterRef("repository_id"),
        project__workspace_id=F("connection__workspace_id"),
        project__deleted_at__isnull=True,
    )
    waiting = (
        GitHubWebhookDelivery.objects.filter(
            status__in=["waiting", "awaiting_mapping"],
        )
        .annotate(has_mapping=Exists(eligible_mapping))
        .filter(has_mapping=True)
        .order_by(
            "received_at",
        )
        .values_list("id", flat=True)[:100]
    )
    for delivery_id in list(pending) + list(waiting):
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
