# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Real-time work item sync: publish curated issue events to Redis.

When a work item (or its assignees/labels) changes, a small JSON payload is
published to the ``gsd:workitem-events`` Redis channel. The live service
(apps/live) subscribes to that channel and fans the event out to websocket
clients so every viewer of the item sees the change without a reload.

Publishing is inline (no celery task) and debounced per process by 300ms per
issue — the latest payload for an issue wins.

Import-order safety: this module is imported from ``plane/urls.py`` and
``plane/celery.py``. ``plane/celery.py`` is imported BEFORE ``django.setup()``
(see ``plane/__init__.py``), so no model or serializer imports happen at module
level here. Signal receivers are registered without a sender filter and resolve
(and cache) the model classes lazily on first fire.
"""

# Python imports
import json
import threading
from typing import Any, Dict, Optional, Tuple

# Django imports
from django.db.models.signals import m2m_changed, post_delete, post_save
from django.dispatch import receiver

# Module imports
from plane.utils.exception_logger import log_exception

# Redis channel the live service subscribes to (see
# apps/api/plane/app/serializers/workitem_realtime.py).
WORKITEM_EVENTS_CHANNEL = "gsd:workitem-events"

# Per-issue publish debounce window (seconds).
DEBOUNCE_SECONDS = 0.3

# Issue actions published on the channel.
ACTION_CREATED = "created"
ACTION_UPDATED = "updated"
ACTION_DELETED = "deleted"

# Lazy model cache — populated on first signal fire (post django.setup()).
_models_cache: Dict[str, Any] = {}

# Lazily created (and cached) redis client used for publishing.
_redis_client: Any = None
_redis_client_lock = threading.Lock()

# Debounce bookkeeping: issue_id -> (timer, latest payload)
_publish_timers: Dict[str, Tuple[threading.Timer, Dict[str, Any]]] = {}
_publish_timers_lock = threading.Lock()


def _models() -> Dict[str, Any]:
    """Resolve (and cache) the model classes this module reacts to."""
    if not _models_cache:
        from plane.db.models import Issue, IssueAssignee, IssueLabel

        _models_cache.update(issue=Issue, issue_assignee=IssueAssignee, issue_label=IssueLabel)
    return _models_cache


def _get_redis_client() -> Any:
    """Lazily create (and cache) the redis client used for publishing."""
    global _redis_client
    with _redis_client_lock:
        if _redis_client is None:
            from plane.settings.redis import redis_instance

            _redis_client = redis_instance()
        return _redis_client


def _publish(payload: Dict[str, Any]) -> None:
    """Publish a serialized work item event. Never raises."""
    try:
        _get_redis_client().publish(WORKITEM_EVENTS_CHANNEL, json.dumps(payload, default=str))
    except Exception as error:
        log_exception(error, warning=True)


def _flush_publish(issue_id: str) -> None:
    """Timer callback: publish the latest debounced payload for the issue."""
    with _publish_timers_lock:
        pending = _publish_timers.pop(issue_id, None)
    if pending is None:
        return
    _publish(pending[1])


def _schedule_publish(issue_id: str, payload: Dict[str, Any]) -> None:
    """Debounce publishes per issue: the latest payload wins."""
    with _publish_timers_lock:
        pending = _publish_timers.get(issue_id)
        if pending is not None:
            pending[0].cancel()
        timer = threading.Timer(DEBOUNCE_SECONDS, _flush_publish, args=(issue_id,))
        timer.daemon = True
        _publish_timers[issue_id] = (timer, payload)
        timer.start()


def _through_actor_id(instance: Any) -> Optional[str]:
    """Best-effort actor for assignee/label row changes."""
    actor_id = str(getattr(instance, "updated_by_id", None) or getattr(instance, "created_by_id", None) or "")
    return actor_id or None


def _issue_changed(
    issue: Any,
    action: str,
    actor_id: Optional[str] = None,
    assignee_ids: Optional[list] = None,
    label_ids: Optional[list] = None,
) -> None:
    """Serialize the issue and schedule a debounced publish. Never raises."""
    try:
        if issue is None:
            return
        # Imported lazily: model-dependent modules must not load before
        # django.setup() (this module is imported by plane/celery.py pre-setup).
        from plane.app.serializers.workitem_realtime import serialize_workitem_event

        payload = serialize_workitem_event(
            issue,
            action=action,
            actor_id=actor_id,
            assignee_ids=assignee_ids,
            label_ids=label_ids,
        )
        _schedule_publish(str(issue.id), payload)
    except Exception as error:
        log_exception(error, warning=True)


def _issue_deleted(issue: Any) -> None:
    """Serialize a deleted issue and publish immediately (no debounce)."""
    try:
        if issue is None:
            return
        from plane.app.serializers.workitem_realtime import serialize_workitem_event

        _publish(serialize_workitem_event(issue, action=ACTION_DELETED))
    except Exception as error:
        log_exception(error, warning=True)


# Receivers are registered without a sender filter because the module is
# imported before django.setup() (model classes are not available yet). Each
# handler filters on the lazily resolved sender instead.
@receiver(post_save, dispatch_uid="gsd_workitem_issue_post_save")
def issue_post_save(sender: Any, instance: Any, created: bool, raw: bool = False, **kwargs: Any) -> None:
    if raw or sender is not _models().get("issue"):
        return
    _issue_changed(instance, ACTION_CREATED if created else ACTION_UPDATED)


@receiver(post_delete, dispatch_uid="gsd_workitem_issue_post_delete")
def issue_post_delete(sender: Any, instance: Any, **kwargs: Any) -> None:
    if sender is not _models().get("issue"):
        return
    _issue_deleted(instance)


@receiver(post_save, dispatch_uid="gsd_workitem_issue_assignee_post_save")
def issue_assignee_post_save(sender: Any, instance: Any, created: bool, raw: bool = False, **kwargs: Any) -> None:
    if raw or sender is not _models().get("issue_assignee"):
        return
    # bulk_create does not fire signals; this covers assignee rows created or
    # updated individually (APIs that bypass IssueSerializer).
    _issue_changed(instance.issue, ACTION_UPDATED, actor_id=_through_actor_id(instance))


@receiver(post_delete, dispatch_uid="gsd_workitem_issue_assignee_post_delete")
def issue_assignee_post_delete(sender: Any, instance: Any, **kwargs: Any) -> None:
    if sender is not _models().get("issue_assignee"):
        return
    # Serialized after the delete, so the fresh assignee id list is published.
    _issue_changed(instance.issue, ACTION_UPDATED, actor_id=_through_actor_id(instance))


@receiver(post_save, dispatch_uid="gsd_workitem_issue_label_post_save")
def issue_label_post_save(sender: Any, instance: Any, created: bool, raw: bool = False, **kwargs: Any) -> None:
    if raw or sender is not _models().get("issue_label"):
        return
    _issue_changed(instance.issue, ACTION_UPDATED, actor_id=_through_actor_id(instance))


@receiver(post_delete, dispatch_uid="gsd_workitem_issue_label_post_delete")
def issue_label_post_delete(sender: Any, instance: Any, **kwargs: Any) -> None:
    if sender is not _models().get("issue_label"):
        return
    _issue_changed(instance.issue, ACTION_UPDATED, actor_id=_through_actor_id(instance))


def _handle_through_m2m_change(sender: Any, instance: Any, action: str, reverse: bool, pk_set: Any) -> None:
    if action not in ("post_add", "post_remove", "post_clear"):
        return
    issue_model = _models().get("issue")
    if issue_model is None:
        return
    if reverse:
        # Reverse relation: instance is a User, refresh each affected issue.
        for issue in issue_model.objects.filter(pk__in=pk_set or []):
            _issue_changed(issue, ACTION_UPDATED)
    else:
        _issue_changed(instance, ACTION_UPDATED)


@receiver(m2m_changed, dispatch_uid="gsd_workitem_issue_assignees_m2m")
def issue_assignees_m2m_changed(sender: Any, instance: Any, action: str, reverse: bool = False, pk_set: Any = None, **kwargs: Any) -> None:
    # Through-model rows are usually created/deleted directly (bulk_create does
    # not fire signals), so this covers genuine m2m usage of Issue.assignees.
    if sender is not _models().get("issue_assignee"):
        return
    _handle_through_m2m_change(sender, instance, action, reverse, pk_set)


@receiver(m2m_changed, dispatch_uid="gsd_workitem_issue_labels_m2m")
def issue_labels_m2m_changed(sender: Any, instance: Any, action: str, reverse: bool = False, pk_set: Any = None, **kwargs: Any) -> None:
    if sender is not _models().get("issue_label"):
        return
    _handle_through_m2m_change(sender, instance, action, reverse, pk_set)
