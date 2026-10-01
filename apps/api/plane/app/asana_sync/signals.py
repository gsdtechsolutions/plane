# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Near-real-time Plane -> Asana push triggers.

Receivers on Issue / IssueAssignee / IssueComment enqueue one targeted push
task per eligible sync. The sync engine wraps its own pull-side writes in
``suppress_asana_sync`` so mirrored changes never echo back as pushes (same
pattern as slack_delivery.notify). Targeted push tasks respect the shared
``asana_sync_lock``: when a full pass holds it, they skip and the 5-minute
beat tick catches up.

Known limitation (mirrors slack_delivery.notify): bulk assignee replacement
via issue serializers (delete + bulk_create on the through model) bypasses
model signals, so only row-level and set/add/remove assignee writes push.
"""

from contextlib import contextmanager
from contextvars import ContextVar

from django.db import transaction
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from plane.utils.exception_logger import log_exception

_suppressed = ContextVar("asana_sync_suppressed", default=False)


@contextmanager
def suppress_asana_sync():
    token = _suppressed.set(True)
    try:
        yield
    finally:
        _suppressed.reset(token)


def _eligible_sync_ids(project_id) -> list[str]:
    from plane.db.models import AsanaProjectSync

    return [
        str(sid)
        for sid in AsanaProjectSync.objects.filter(
            project_id=project_id,
            is_active=True,
            connection__is_active=True,
            connection__deleted_at__isnull=True,
            deleted_at__isnull=True,
            direction__in=("push", "bidirectional"),
        ).values_list("id", flat=True)
    ]


def _schedule(issue) -> None:
    sync_ids = _eligible_sync_ids(issue.project_id)
    if not sync_ids:
        return
    from plane.app.asana_sync.tasks import asana_sync_push_issue

    for sync_id in sync_ids:
        transaction.on_commit(
            lambda sid=sync_id, iid=str(issue.id): asana_sync_push_issue.apply_async(args=[sid, iid])
        )


def _issue_is_pushable(issue) -> bool:
    return not issue.is_draft and issue.archived_at is None and issue.deleted_at is None


@receiver(post_save, dispatch_uid="asana_sync_issue_post_save")
def issue_post_save(sender, instance, raw=False, **kwargs):
    if raw or _suppressed.get() or sender._meta.label_lower != "db.issue":
        return
    if not _issue_is_pushable(instance):
        return
    try:
        _schedule(instance)
    except Exception:
        log_exception("asana_sync issue_post_save failed")


@receiver(post_save, dispatch_uid="asana_sync_issue_assignee_post_save")
def issue_assignee_post_save(sender, instance, created, raw=False, **kwargs):
    if raw or not created or _suppressed.get() or sender._meta.label_lower != "db.issueassignee":
        return
    try:
        issue = instance.issue
        if not _issue_is_pushable(issue):
            return
        _schedule(issue)
    except Exception:
        log_exception("asana_sync issue_assignee_post_save failed")


@receiver(post_delete, dispatch_uid="asana_sync_issue_assignee_post_delete")
def issue_assignee_post_delete(sender, instance, **kwargs):
    """Unassignment deletes through rows; push so the Asana task is unassigned too."""
    if _suppressed.get() or sender._meta.label_lower != "db.issueassignee":
        return
    try:
        issue = instance.issue
        if not _issue_is_pushable(issue):
            return
        _schedule(issue)
    except Exception:
        log_exception("asana_sync issue_assignee_post_delete failed")


@receiver(post_save, dispatch_uid="asana_sync_comment_post_save")
def comment_post_save(sender, instance, created, raw=False, **kwargs):
    if raw or not created or _suppressed.get() or sender._meta.label_lower != "db.issuecomment":
        return
    if instance.external_source and str(instance.external_source).startswith("asana:"):
        return
    try:
        issue = instance.issue
        if not _issue_is_pushable(issue):
            return
        _schedule(issue)
    except Exception:
        log_exception("asana_sync comment_post_save failed")


def _schedule_assignee_push(value) -> None:
    """Enqueue a forced assignee push for syncs whose mirror property changed.

    Property writes do not bump issue.updated_at, so the regular push paths'
    delta guard would skip them — a dedicated task pushes the assignment."""
    from plane.db.models import AsanaProjectSync, Issue

    issue_id = value.issue_id
    property_id = value.property_id
    if issue_id is None or property_id is None:
        return
    project_id = Issue.objects.filter(id=issue_id).values_list("project_id", flat=True).first()
    if project_id is None:
        return
    sync_ids = [
        str(sid)
        for sid in AsanaProjectSync.objects.filter(
            project_id=project_id,
            assignee_property_id=property_id,
            is_active=True,
            connection__is_active=True,
            connection__deleted_at__isnull=True,
            deleted_at__isnull=True,
        ).values_list("id", flat=True)
    ]
    if not sync_ids:
        return
    from plane.app.asana_sync.tasks import asana_sync_assignee_property_changed

    for sync_id in sync_ids:
        transaction.on_commit(
            lambda sid=sync_id, iid=str(issue_id): asana_sync_assignee_property_changed.apply_async(
                args=[sid, iid]
            )
        )


@receiver(post_save, dispatch_uid="asana_sync_assignee_property_post_save")
def assignee_property_value_post_save(sender, instance, raw=False, **kwargs):
    if raw or _suppressed.get() or sender._meta.label_lower != "db.custompropertyvalue":
        return
    try:
        _schedule_assignee_push(instance)
    except Exception:
        log_exception("asana_sync assignee_property_value_post_save failed")


@receiver(post_delete, dispatch_uid="asana_sync_assignee_property_post_delete")
def assignee_property_value_post_delete(sender, instance, **kwargs):
    if _suppressed.get() or sender._meta.label_lower != "db.custompropertyvalue":
        return
    try:
        _schedule_assignee_push(instance)
    except Exception:
        log_exception("asana_sync assignee_property_value_post_delete failed")
