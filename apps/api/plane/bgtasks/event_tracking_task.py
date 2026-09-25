# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Fork customization: conventional-commit auto-labeling hook.

This module owns the Django signal wiring that auto-labels work items with
conventional-commit labels (feat/fix/docs/refactor/chore/...) derived from the
title prefix whenever a work item is created or renamed in a project that has
`ProjectCustomSettings.auto_conventional_commit_labels` enabled.

The label helpers themselves live in `plane.utils.conventional_commits`; this
module only captures the previous title on updates and defers to the helper on
save. Registration is idempotent and guarded so it can be imported from the
URLconf (see plane.app.urls.issue) without touching upstream serializers.

NOTE: the fork-side removal of PostHog event tracking means preview no longer
ships a posthog `track_event` task; this module deliberately keeps the upstream
filename slot for fork automation hooks only.
"""

import logging

# Django imports
from django.db.models.signals import post_save, pre_save

# Module imports
from plane.utils.exception_logger import log_exception

logger = logging.getLogger("plane.worker")

# Per-process cache of the previous title for work items being updated, so the
# post_save receiver can detect a rename without re-querying after the fact.
_ISSUE_PREVIOUS_NAME_CACHE: dict = {}

_signals_registered = False


def _capture_previous_name(sender, instance, **kwargs):
    """
    @description pre_save receiver that remembers the work item's previous title so
    a rename away from a conventional-commit prefix can be detected.
    @param {Issue} sender - The Issue model class.
    @param {Issue} instance - The Issue instance about to be saved.
    """
    from plane.db.models import Issue

    if not isinstance(instance, Issue) or instance.pk is None or instance.deleted_at is not None:
        return
    try:
        previous_name = (
            Issue.objects.filter(pk=instance.pk).values_list("name", flat=True).first()
        )
    except Exception as e:
        log_exception(e)
        previous_name = None
    _ISSUE_PREVIOUS_NAME_CACHE[instance.pk] = previous_name


def _apply_label_on_save(sender, instance, created, **kwargs):
    """
    @description post_save receiver that applies the conventional-commit label for the
    work item title. Skips soft-deleted work items and saves that did not rename
    the work item. Any failure is logged and swallowed so work item saves never
    break because of labeling.
    @param {Issue} sender - The Issue model class.
    @param {Issue} instance - The saved Issue instance.
    @param {bool} created - Whether this save created the work item.
    """
    from plane.db.models import Issue
    from plane.utils.conventional_commits import apply_conventional_commit_label

    if not isinstance(instance, Issue):
        return

    previous_name = _ISSUE_PREVIOUS_NAME_CACHE.pop(instance.pk, None) if instance.pk else None

    # Never label soft-deleted work items; never label when the title is unchanged
    if instance.deleted_at is not None or (not created and previous_name == instance.name):
        return

    try:
        apply_conventional_commit_label(instance, old_title=previous_name)
    except ImportError:
        # ProjectCustomSettings (sidecar model) is not available yet — labeling is a no-op.
        return
    except Exception as e:
        log_exception(e)


def register_conventional_commit_label_signals():
    """
    @description Idempotently connect the auto-label receivers to the Issue model signals.
    Safe to call multiple times and safe to import before the app registry is
    ready (model imports happen lazily inside the receivers).
    """
    global _signals_registered
    if _signals_registered:
        return
    try:
        pre_save.connect(_capture_previous_name, sender="db.Issue", dispatch_uid="orca_conventional_commit_pre_save")
        post_save.connect(_apply_label_on_save, sender="db.Issue", dispatch_uid="orca_conventional_commit_post_save")
        _signals_registered = True
    except Exception as e:
        log_exception(e)


# Register on import so a single import line anywhere at startup (URLconf) is enough.
register_conventional_commit_label_signals()
