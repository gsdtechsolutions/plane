# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""One-time (idempotent) backfill of Issue.external_source/external_id from
AsanaTaskLink mappings.

The sync engine stamps identity at creation and looks it up before creating
anything, but items mirrored before that change carry no external_id — without
the backfill, a lost link still lets the next pass adopt nothing and duplicate.
Writes go through queryset .update(): no signals, no updated_at bump, so the
LWW loop guard sees no local change.
"""

# Django imports
from django.core.management.base import BaseCommand, CommandError

# Module imports
from plane.db.models import AsanaProjectSync, AsanaTaskLink, Issue


class Command(BaseCommand):
    help = (
        "Stamp Issue.external_source='asana' / external_id=<task gid> from "
        "asana_task_links so the sync can adopt instead of duplicate. Idempotent."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--sync",
            help="AsanaProjectSync id (default: every active, non-deleted sync)",
        )
        parser.add_argument(
            "--exclude-seq",
            default="",
            help="Comma-separated issue sequence_ids to skip (e.g. cancelled duplicates)",
        )
        parser.add_argument(
            "--map",
            action="append",
            default=[],
            metavar="SEQ=GID",
            help="Force external_id for an issue sequence_id, overriding the link-derived gid",
        )
        parser.add_argument("--dry-run", action="store_true", help="Print without writing")

    def handle(self, *args, **options):
        syncs = AsanaProjectSync.objects.filter(is_active=True, deleted_at__isnull=True)
        if options["sync"]:
            syncs = syncs.filter(id=options["sync"])
        exclude = {int(x) for x in options["exclude_seq"].split(",") if x.strip()}
        overrides = {}
        for raw in options["map"]:
            try:
                seq, gid = raw.split("=", 1)
                overrides[int(seq)] = gid.strip()
            except ValueError:
                raise CommandError(f"--map expects SEQ=GID, got {raw!r}")

        for sync in syncs:
            # Oldest link per issue wins: for a kept item whose later link was
            # re-pointed, the original mapping is the authoritative one.
            chosen: dict = {}
            # all_objects: soft-deleted links are exactly the history we are
            # repairing (the default manager hides them).
            for link in AsanaTaskLink.all_objects.filter(sync=sync).select_related("issue").order_by(
                "created_at"
            ):
                issue = link.issue
                if issue is None or issue.deleted_at:
                    continue
                chosen.setdefault(issue.id, (issue, link.asana_task_gid))

            stamped = 0
            for issue, gid in chosen.values():
                if issue.sequence_id in exclude:
                    self.stdout.write(f"sync {sync.id}: skip FR-{issue.sequence_id} (excluded)")
                    continue
                gid = overrides.get(issue.sequence_id, gid)
                if issue.external_source == "asana" and issue.external_id == gid:
                    continue
                self.stdout.write(f"sync {sync.id}: FR-{issue.sequence_id} -> {gid}")
                if not options["dry_run"]:
                    Issue.objects.filter(id=issue.id).update(
                        external_source="asana", external_id=gid
                    )
                stamped += 1
            self.stdout.write(
                self.style.SUCCESS(
                    f"sync {sync.id} ({sync.project.identifier}): "
                    f"{stamped} stamped, {len(exclude)} excluded, "
                    f"{len(chosen) - stamped} already current"
                )
            )
