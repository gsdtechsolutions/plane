# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""One-time (idempotent) backfill of the Asana assignee mirror property.

Existing issues already carry the assignment as a Plane member (native
assignee) or a person label (Asana-only people) — both keyed by assignee_map.
This command derives the property value from that data, so no Asana API calls
are needed and the LWW timestamps stay untouched. The property itself and its
people options are provisioned if missing."""

# Django imports
from django.core.management.base import BaseCommand

# Module imports
from plane.app.asana_sync.engine import (
    ensure_assignee_property,
    sync_assignee_property_options,
)
from plane.db.models import AsanaProjectSync, AsanaTaskLink, CustomPropertyValue


class Command(BaseCommand):
    help = (
        "Backfill the 'Asana Assignee' mirror property from each linked "
        "issue's current assignee / person label. Idempotent, no Asana calls."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--sync",
            help="AsanaProjectSync id (default: every active, non-deleted sync)",
        )
        parser.add_argument("--dry-run", action="store_true", help="Print without writing")

    def handle(self, *args, **options):
        syncs = AsanaProjectSync.objects.filter(is_active=True, deleted_at__isnull=True)
        if options["sync"]:
            syncs = syncs.filter(id=options["sync"])

        for sync in syncs:
            prop = ensure_assignee_property(sync)
            if prop is None:
                self.stdout.write(f"sync {sync.id}: assignee property inactive, skipped")
                continue
            sync_assignee_property_options(sync, prop)
            raws = {str(v) for v in (sync.assignee_map or {}).values() if v}

            stamped = cleared = unchanged = 0
            links = AsanaTaskLink.objects.filter(sync=sync, deleted_at__isnull=True).select_related(
                "issue"
            )
            for link in links:
                issue = link.issue
                if issue is None or issue.deleted_at:
                    continue
                raw = None
                member = issue.assignees.first()
                if member is not None and f"member:{member.id}" in raws:
                    raw = f"member:{member.id}"
                else:
                    for label in issue.labels.all():
                        if f"label:{label.id}" in raws:
                            raw = f"label:{label.id}"
                            break
                desired = [raw] if raw else []
                row = CustomPropertyValue.objects.filter(
                    property=prop, issue=issue, deleted_at__isnull=True
                ).first()
                current = list(row.value_option or []) if row else []
                if current == desired:
                    unchanged += 1
                    continue
                self.stdout.write(
                    f"sync {sync.id}: {issue.sequence_id} -> {raw or '(none)'}"
                )
                if not options["dry_run"]:
                    if not desired:
                        if row is not None:
                            row.delete(soft=False)
                            cleared += 1
                    elif row is None:
                        CustomPropertyValue.objects.create(
                            property=prop, issue=issue, value_option=desired
                        )
                        stamped += 1
                    else:
                        row.value_option = desired
                        row.save(update_fields=["value_option", "updated_at"])
                        stamped += 1
            self.stdout.write(
                self.style.SUCCESS(
                    f"sync {sync.id} ({sync.project.identifier}): property '{prop.name}' — "
                    f"{stamped} set, {cleared} cleared, {unchanged} unchanged"
                )
            )
