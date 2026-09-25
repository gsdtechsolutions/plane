# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Persist trigger deliveries with source writes; apply each rule atomically after commit.

Serializer updates snapshot before relation replacement. Signals cover ordinary
model state saves and individual/M2M assignee additions. Automation writes are
guarded synchronously; later activity-task timestamp saves have no field diff.
Pending/failed execution rows can be retried with execute_execution(id).
"""

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date
from functools import wraps
from uuid import uuid4

from django.db import transaction
from django.db.models import Q
from django.db.models.signals import m2m_changed, post_save, pre_save
from django.dispatch import receiver

from plane.utils.exception_logger import log_exception

_suppressed = ContextVar("automation_suppressed", default=False)


@contextmanager
def suppress_automations():
    token = _suppressed.set(True)
    try:
        yield
    finally:
        _suppressed.reset(token)


def _snapshot(issue):
    return issue.state_id, set(issue.issue_assignee.values_list("assignee_id", flat=True))


def automation_update(method):
    """Wrap serializer updates before they delete/bulk-create relation rows."""

    @wraps(method)
    def wrapped(self, instance, validated_data):
        from plane.db.models import Issue

        if _suppressed.get():
            return method(self, instance, validated_data)
        with transaction.atomic():
            # Serialize competing mutations and snapshot the current DB state.
            locked = Issue.objects.select_for_update().get(pk=instance.pk)
            before = _snapshot(locked)
            with suppress_automations():
                result = method(self, locked, validated_data)
            after = _snapshot(result)
            _record_changes(result, before, after)
        # Synchronous after-commit actions may change fields on another instance.
        result.refresh_from_db()
        return result

    return wrapped


def automation_bulk_update(method):
    """Capture board bulk operations, which bypass model save signals."""

    @wraps(method)
    def wrapped(self, request, slug, project_id):
        from plane.db.models import Issue

        with transaction.atomic():
            issues = list(
                Issue.objects.select_for_update()
                .filter(
                    workspace__slug=slug,
                    project_id=project_id,
                    pk__in=request.data.get("issue_ids", []),
                )
                .order_by("pk")
            )
            before = {issue.pk: _snapshot(issue) for issue in issues}
            with suppress_automations():
                response = method(self, request, slug, project_id)
            if response.status_code >= 400:
                transaction.set_rollback(True)
                return response
            for issue in issues:
                issue.refresh_from_db()
                _record_changes(issue, before[issue.pk], _snapshot(issue))
        return response

    return wrapped


def _record_changes(issue, before, after):
    old_state, old_members = before
    state, members = after
    triggers = []
    if old_state != state:
        triggers.append(("state_changed", state))
    triggers.extend(("assignee_added", member) for member in members - old_members)
    if triggers:
        _record_triggers(issue, triggers)


def _record_triggers(issue, triggers):
    from plane.db.models import AutomationExecution, AutomationRule

    event_id = uuid4()
    rule_ids = set()
    for trigger, value in triggers:
        rule_ids.update(
            AutomationRule.objects.filter(
                Q(trigger_value__isnull=True) | Q(trigger_value=value),
                project_id=issue.project_id,
                workspace_id=issue.workspace_id,
                trigger_type=trigger,
                is_active=True,
            ).values_list("id", flat=True)
        )
    for rule_id in sorted(rule_ids, key=str):
        execution = AutomationExecution.objects.create(
            event_id=event_id,
            rule_id=rule_id,
            issue_id=issue.pk,
        )
        transaction.on_commit(lambda pk=execution.pk: execute_execution(pk), robust=True)


@receiver(pre_save, dispatch_uid="gsd_automations_issue_pre_save")
def issue_pre_save(sender, instance, raw=False, update_fields=None, **kwargs):
    if raw or _suppressed.get() or sender._meta.label_lower != "db.issue":
        return
    instance._automation_old_state = None
    if instance._state.adding or (update_fields is not None and not {"state", "state_id"}.intersection(update_fields)):
        return
    instance._automation_old_state = sender.objects.filter(pk=instance.pk).values_list("state_id", flat=True).first()


@receiver(post_save, dispatch_uid="gsd_automations_issue_post_save")
def issue_post_save(sender, instance, created, raw=False, **kwargs):
    if raw or created or _suppressed.get() or sender._meta.label_lower != "db.issue":
        return
    old = getattr(instance, "_automation_old_state", None)
    if old is not None and old != instance.state_id:
        _record_triggers(instance, [("state_changed", instance.state_id)])


@receiver(post_save, dispatch_uid="gsd_automations_issue_assignee_post_save")
def issue_assignee_post_save(sender, instance, created, raw=False, **kwargs):
    if raw or not created or _suppressed.get() or sender._meta.label_lower != "db.issueassignee":
        return
    _record_triggers(instance.issue, [("assignee_added", instance.assignee_id)])


@receiver(m2m_changed, dispatch_uid="gsd_automations_issue_assignees_m2m")
def issue_assignees_m2m_changed(sender, instance, action, reverse=False, pk_set=None, **kwargs):
    if _suppressed.get() or action != "post_add" or sender._meta.label_lower != "db.issueassignee":
        return
    if reverse:
        from plane.db.models import Issue

        for issue in Issue.objects.filter(pk__in=pk_set or []):
            _record_triggers(issue, [("assignee_added", instance.pk)])
    else:
        _record_triggers(instance, [("assignee_added", member) for member in pk_set or []])


def execute_execution(execution_id):
    """Retryable delivery: actions and success marker commit in one transaction."""
    from plane.db.models import AutomationExecution, AutomationRule, Issue
    from plane.app.automations.serializers import AutomationRuleSerializer

    with transaction.atomic():
        execution = AutomationExecution.objects.select_for_update().filter(pk=execution_id).first()
        if execution is None:
            return
        if execution.status in ("succeeded", "skipped"):
            return
        # Source mutations also lock issue first; rule writes never lock issues.
        issue = Issue.objects.select_for_update().filter(pk=execution.issue_id).first()
        rule = AutomationRule.objects.select_for_update().filter(pk=execution.rule_id, is_active=True).first()
        if (
            issue is None
            or rule is None
            or rule.project_id != issue.project_id
            or rule.workspace_id != issue.workspace_id
        ):
            execution.status = "skipped"
            execution.error = ""
        else:
            try:
                # Savepoint rolls back every action if any action fails.
                with transaction.atomic(), suppress_automations():
                    validator = AutomationRuleSerializer(
                        rule, data={"actions": rule.actions}, partial=True, context={"project": issue.project}
                    )
                    validator.is_valid(raise_exception=True)
                    _apply_actions(rule, issue)
                execution.status = "succeeded"
                execution.error = ""
            except Exception as error:
                execution.status = "failed"
                execution.error = type(error).__name__
                log_exception(error, warning=True)
        execution.save(update_fields=["status", "error", "updated_at"])


def _apply_actions(rule, issue):
    from plane.db.models import IssueAssignee, IssueLabel, State

    dirty = set()
    relations_changed = False
    for action in rule.actions:
        kind, value = action["type"], action["value"]
        if kind == "set_state" and str(issue.state_id) != str(value):
            issue.state = State.objects.get(pk=value, project_id=issue.project_id)
            dirty.add("state")
        elif kind == "set_priority" and issue.priority != value:
            issue.priority = value
            dirty.add("priority")
        elif kind == "set_due_date" and issue.target_date != date.fromisoformat(value):
            issue.target_date = date.fromisoformat(value)
            dirty.add("target_date")
        elif kind == "add_label":
            _, created = IssueLabel.objects.get_or_create(
                issue=issue,
                label_id=value,
                defaults={
                    "project_id": issue.project_id,
                    "workspace_id": issue.workspace_id,
                },
            )
            relations_changed |= created
        elif kind == "remove_label":
            removed = IssueLabel.objects.filter(issue=issue, label_id=value).delete()
            relations_changed |= bool(removed)
        elif kind == "assign_member":
            _, created = IssueAssignee.objects.get_or_create(
                issue=issue,
                assignee_id=value,
                defaults={
                    "project_id": issue.project_id,
                    "workspace_id": issue.workspace_id,
                },
            )
            relations_changed |= created
    if dirty or relations_changed:
        issue.save(update_fields=sorted(dirty | {"updated_at"}))
