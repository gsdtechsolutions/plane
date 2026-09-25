# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Board automations executor: evaluate per-project WHEN/THEN rules on work items.

Import-order safety mirrors ``bgtasks/workitem_realtime.py``: this module is
imported from ``plane/urls.py`` and ``plane/celery.py`` — and ``plane/celery.py``
runs BEFORE ``django.setup()`` (see ``plane/__init__.py``) — so no model imports
happen at module level. Signal receivers are registered without a sender filter
and resolve (and cache) the model classes lazily on first fire.

Semantics (SPECS/automations.md):

- ``state_changed`` fires when a persisted state transition old != new is
  committed and (rule.trigger_value is null OR new state == trigger_value).
  Work item creation fires neither trigger: there is no prior state to
  transition from, and assignees set at creation are not "added" members.
- ``assignee_added`` fires when a member's persisted assignee row appears
  relative to the PRE-SAVE snapshot of persisted assignee ids. The
  snapshot/compare design exists because serializer paths bulk-replace assignee
  rows (delete + ``bulk_create``), which emits no relation signals.
- Rules run AFTER the surrounding transaction commits (``transaction.on_commit``),
  so rolled-back or partially-persisted saves never trigger rules and actions
  never observe mid-request state.
- Loop guard: a threadlocal flag is set while a rule applies its actions; every
  receiver short-circuits while it is set, so system-applied changes never
  re-trigger rules (cascade prevention, including nested signal storms). The
  automated writes still flow into the existing real-time events
  (``bgtasks/workitem_realtime.py`` is untouched and its receivers keep firing).
- Bookkeeping-only saves (e.g. the async issue-activity task touching
  ``updated_at``) are inert by construction: both triggers require an actual
  field/row diff, not merely a save event.
- Idempotency: actions skip writes that are already satisfied, and
  execution-time revalidation gracefully skips actions whose referenced
  state/label/member no longer exists.
"""

# Python imports
import threading
from datetime import date
from typing import Any, Dict, FrozenSet, List, Optional, Set

# Django imports
from django.db import transaction
from django.db.models import Q
from django.db.models.signals import m2m_changed, post_save, pre_save
from django.dispatch import receiver

# Module imports
from plane.utils.exception_logger import log_exception

# Lazy model cache — populated on first signal fire (post django.setup()).
_models_cache: Dict[str, Any] = {}


def _models() -> Dict[str, Any]:
    """Resolve (and cache) the model classes this module reacts to."""
    if not _models_cache:
        from plane.db.models import Issue, IssueAssignee

        _models_cache.update(issue=Issue, issue_assignee=IssueAssignee)
    return _models_cache


# ---------------------------------------------------------------------------
# Loop guard: system-applied changes must not re-trigger rules.
# ---------------------------------------------------------------------------
_guard = threading.local()


def _is_executing() -> bool:
    return bool(getattr(_guard, "executing", False))


class _RuleExecution:
    """Context manager marking the current thread as applying automation actions."""

    def __enter__(self) -> "_RuleExecution":
        _guard.executing = True
        return self

    def __exit__(self, *args: Any) -> None:
        _guard.executing = False


# ---------------------------------------------------------------------------
# Pre-save snapshots (threadlocal): issue_id -> frozenset of persisted assignee
# ids before the write. Used by the post_save on-commit compare so that
# bulk-replaced assignee rows (no signals) are still detected. Overwritten on
# every pre_save; popped when consumed by the on-commit evaluation.
# ---------------------------------------------------------------------------
def _armed() -> Dict[str, FrozenSet[Any]]:
    armed = getattr(_guard, "armed", None)
    if armed is None:
        armed = _guard.armed = {}
    return armed


def _persisted_assignee_ids(issue: Any) -> FrozenSet[Any]:
    from plane.db.models import IssueAssignee

    return frozenset(
        IssueAssignee.objects.filter(issue=issue, deleted_at__isnull=True).values_list("assignee_id", flat=True)
    )


# ---------------------------------------------------------------------------
# Receivers
# ---------------------------------------------------------------------------
@receiver(pre_save, dispatch_uid="gsd_automations_issue_pre_save")
def issue_pre_save(sender: Any, instance: Any, raw: bool = False, **kwargs: Any) -> None:
    """Snapshot the old state and persisted assignees before the write."""
    if raw or sender is not _models().get("issue") or _is_executing():
        return
    try:
        # Remember the previous state for the post_save transition diff.
        instance._automation_prev_state_id = instance.state_id
        if instance._state.adding:
            return
        _armed()[str(instance.id)] = _persisted_assignee_ids(instance)
    except Exception as error:
        log_exception(error, warning=True)


@receiver(post_save, dispatch_uid="gsd_automations_issue_post_save")
def issue_post_save(sender: Any, instance: Any, created: bool, raw: bool = False, **kwargs: Any) -> None:
    if raw or sender is not _models().get("issue") or _is_executing():
        return
    try:
        issue_key = str(instance.id)
        if created:
            # New work items fire neither trigger (see module docstring).
            _armed().pop(issue_key, None)
            return

        prev_state_id = getattr(instance, "_automation_prev_state_id", None)
        new_state_id = instance.state_id
        state_changed = (
            prev_state_id is not None
            and new_state_id is not None
            and str(prev_state_id) != str(new_state_id)
        )
        # Consume the pre-save assignee snapshot (None means "not armed", e.g.
        # a save from a guarded execution path that skipped pre_save arming).
        old_assignee_ids: Optional[FrozenSet[Any]] = _armed().pop(issue_key, None)

        if not state_changed and old_assignee_ids is None:
            # Bookkeeping-only save (updated_at and friends): inert by design.
            return

        issue_pk = instance.pk
        transaction.on_commit(lambda: _evaluate_issue(issue_pk, prev_state_id, new_state_id, old_assignee_ids))
    except Exception as error:
        log_exception(error, warning=True)


@receiver(post_save, dispatch_uid="gsd_automations_issue_assignee_post_save")
def issue_assignee_post_save(sender: Any, instance: Any, created: bool, raw: bool = False, **kwargs: Any) -> None:
    """Fallback for assignee rows created without a surrounding Issue.save()."""
    if raw or sender is not _models().get("issue_assignee") or _is_executing():
        return
    if not created:
        return
    if str(instance.issue_id) in _armed():
        # An Issue save armed a post-commit assignee compare for this issue;
        # the compare covers this row — skip to avoid duplicate delivery.
        return
    _schedule_assignee_added(str(instance.issue_id), [instance.assignee_id])


@receiver(m2m_changed, dispatch_uid="gsd_automations_issue_assignees_m2m")
def issue_assignees_m2m_changed(sender: Any, instance: Any, action: str, reverse: bool = False, pk_set: Any = None, **kwargs: Any) -> None:
    """Fallback for genuine m2m usage of Issue.assignees (through IssueAssignee)."""
    if action != "post_add" or reverse:
        return
    if sender is not _models().get("issue_assignee"):
        return
    if str(instance.pk) in _armed():
        return
    _schedule_assignee_added(str(instance.pk), list(pk_set or []))


def _schedule_assignee_added(issue_id: str, member_ids: List[Any]) -> None:
    """Queue an assignee_added evaluation for after the transaction commits."""
    try:
        if _is_executing() or not member_ids:
            return
        member_ids = list(member_ids)
        transaction.on_commit(lambda: _evaluate_added_assignees(issue_id, member_ids))
    except Exception as error:
        log_exception(error, warning=True)


# ---------------------------------------------------------------------------
# Evaluation (runs after commit)
# ---------------------------------------------------------------------------
def _active_rules(trigger_type: str, project_id: Any, value: Any) -> List[Any]:
    """Active rules for a project/trigger whose trigger_value is null (any) or matches."""
    from plane.db.models import AutomationRule

    return list(
        AutomationRule.objects.filter(
            Q(trigger_value__isnull=True) | Q(trigger_value=value),
            project_id=project_id,
            trigger_type=trigger_type,
            is_active=True,
            deleted_at__isnull=True,
        )
    )


def _evaluate_issue(issue_pk: Any, prev_state_id: Any, new_state_id: Any, old_assignee_ids: Optional[FrozenSet[Any]]) -> None:
    """Diff the committed change against active rules and apply matching actions."""
    if _is_executing():
        return
    try:
        Issue = _models().get("issue")
        issue = Issue.objects.filter(id=issue_pk).first()
        if issue is None:
            return

        state_changed = (
            prev_state_id is not None
            and new_state_id is not None
            and str(prev_state_id) != str(new_state_id)
        )
        added_member_ids: List[Any] = []
        if old_assignee_ids is not None:
            current_ids = _persisted_assignee_ids(issue)
            added_member_ids = [member_id for member_id in current_ids - old_assignee_ids]

        if not state_changed and not added_member_ids:
            return

        with _RuleExecution():
            dirty_fields: Set[str] = set()
            if state_changed:
                for rule in _active_rules("state_changed", issue.project_id, new_state_id):
                    try:
                        dirty_fields |= _apply_rule_actions(rule, issue)
                    except Exception as error:
                        log_exception(error, warning=True)
            for member_id in added_member_ids:
                for rule in _active_rules("assignee_added", issue.project_id, member_id):
                    try:
                        dirty_fields |= _apply_rule_actions(rule, issue)
                    except Exception as error:
                        log_exception(error, warning=True)

            if dirty_fields:
                # Issue.save() keeps the completed_at invariant itself
                # (_sync_completed_at) and expands update_fields accordingly.
                try:
                    issue.save(update_fields=sorted(dirty_fields))
                except Exception as error:
                    log_exception(error, warning=True)
    except Exception as error:
        log_exception(error, warning=True)


def _evaluate_added_assignees(issue_id: str, member_ids: List[Any]) -> None:
    """Evaluate assignee_added rules for members added outside an Issue.save()."""
    if _is_executing():
        return
    try:
        Issue = _models().get("issue")
        issue = Issue.objects.filter(id=issue_id).first()
        if issue is None:
            return
        with _RuleExecution():
            dirty_fields: Set[str] = set()
            for member_id in member_ids:
                for rule in _active_rules("assignee_added", issue.project_id, member_id):
                    try:
                        dirty_fields |= _apply_rule_actions(rule, issue)
                    except Exception as error:
                        log_exception(error, warning=True)
            if dirty_fields:
                try:
                    issue.save(update_fields=sorted(dirty_fields))
                except Exception as error:
                    log_exception(error, warning=True)
    except Exception as error:
        log_exception(error, warning=True)


# ---------------------------------------------------------------------------
# Action application
# ---------------------------------------------------------------------------
def _label_exists(label_value: Any, issue: Any) -> bool:
    """Project labels, or workspace-level labels usable from the project."""
    from plane.db.models import Label

    return (
        Label.objects.filter(
            Q(project_id=issue.project_id) | Q(project__isnull=True, workspace_id=issue.workspace_id),
            id=label_value,
            deleted_at__isnull=True,
        ).exists()
    )


def _apply_rule_actions(rule: Any, issue: Any) -> Set[str]:
    """Apply a rule's actions in order. Returns changed field names (attnames).

    Idempotent: writes already satisfied are skipped. Execution-time revalidated:
    actions referencing deleted/foreign entities are skipped gracefully.
    """
    Issue = _models().get("issue")
    from plane.db.models import IssueAssignee, IssueLabel, ProjectMember, State

    dirty_fields: Set[str] = set()
    for action in rule.actions or []:
        if not isinstance(action, dict):
            continue
        action_type = action.get("type")
        action_value = action.get("value")
        if action_value in (None, ""):
            continue

        if action_type == "set_state":
            state = State.objects.filter(
                id=action_value, project_id=issue.project_id, deleted_at__isnull=True
            ).first()
            if state is not None and str(issue.state_id) != str(state.id):
                issue.state_id = state.id
                dirty_fields.add("state_id")
        elif action_type == "set_priority":
            valid_priorities = {choice[0] for choice in Issue.PRIORITY_CHOICES}
            if action_value in valid_priorities and issue.priority != action_value:
                issue.priority = action_value
                dirty_fields.add("priority")
        elif action_type == "set_due_date":
            try:
                due_date = date.fromisoformat(str(action_value))
            except (TypeError, ValueError):
                continue
            if issue.target_date != due_date:
                issue.target_date = due_date
                dirty_fields.add("target_date")
        elif action_type == "add_label":
            already = IssueLabel.objects.filter(
                issue=issue, label_id=action_value, deleted_at__isnull=True
            ).exists()
            if _label_exists(action_value, issue) and not already:
                try:
                    IssueLabel.objects.create(
                        issue=issue,
                        label_id=action_value,
                        project_id=issue.project_id,
                        workspace_id=issue.workspace_id,
                    )
                except Exception as error:
                    # Concurrent duplicate write: stay idempotent.
                    log_exception(error, warning=True)
        elif action_type == "remove_label":
            IssueLabel.objects.filter(issue=issue, label_id=action_value).delete()
        elif action_type == "assign_member":
            is_member = ProjectMember.objects.filter(
                member_id=action_value, project_id=issue.project_id, is_active=True
            ).exists()
            already = IssueAssignee.objects.filter(
                issue=issue, assignee_id=action_value, deleted_at__isnull=True
            ).exists()
            if is_member and not already:
                try:
                    IssueAssignee.objects.create(
                        issue=issue,
                        assignee_id=action_value,
                        project_id=issue.project_id,
                        workspace_id=issue.workspace_id,
                    )
                except Exception as error:
                    log_exception(error, warning=True)
        # Unknown action types are skipped defensively (validated at save time).
    return dirty_fields
