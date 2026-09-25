# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Two-way sync engine between an AFFiNE workspace and Plane wiki pages.

Strategy: hash-anchored three-way merge detection.

For every mapped pair we remember the content hashes of both sides at the last
successful sync plus the remote ``updated_at``. On each run:

  affine_changed = sha(affine_markdown_now) != map.affine_content_hash
  plane_changed  = sha(plane_html_now)      != map.plane_content_hash

  neither changed                     -> no-op
  affine changed, plane unchanged     -> PULL  (AFFiNE -> Plane)
  plane changed,  affine unchanged    -> PUSH  (Plane -> AFFiNE)
  both changed                        -> conflict per settings.conflict_strategy
                                           newest_wins   : newer updated_at side wins
                                           affine_wins   : AFFiNE overwrites Plane
                                           plane_wins    : Plane overwrites AFFiNE
                                           (ties on newest_wins resolve to AFFiNE)

Unmapped docs on the AFFiNE side are created as new Plane pages (PULL-create);
unmapped wiki pages in the connected project are created as new AFFiNE docs
(PUSH-create) — the latter only when ``settings.sync_plane_creates`` is truthy,
which is opt-in so an accidental wiki edit cannot flood a curated AFFiNE
workspace. Deletions are surfaced as statuses, never auto-propagated, unless
``settings.sync_deletions`` is set.

Every page is processed in its own transaction; one failure marks that pair
"error" and the run continues. All AFFiNE writes go through the client, and the
connection row records aggregate status for the settings UI.
"""

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Optional

from django.db import transaction
from django.utils import timezone

# Module imports
from plane.affine_sync.client import AffineClient, AffineError
from plane.affine_sync.converters import html_to_markdown, markdown_to_html
from plane.db.models import Page, ProjectPage, Project

# bg task imports are deferred into functions to stay import-safe for migrations

logger = logging.getLogger(__name__)

MAX_TITLE_LENGTH = 500


@dataclass
class SyncStats:
    pulled: int = 0
    pushed: int = 0
    created_plane: int = 0
    created_affine: int = 0
    conflicts: int = 0
    errors: int = 0
    noop: int = 0
    deleted_plane: int = 0
    deleted_affine: int = 0
    error_messages: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "pulled": self.pulled,
            "pushed": self.pushed,
            "created_plane": self.created_plane,
            "created_affine": self.created_affine,
            "conflicts": self.conflicts,
            "errors": self.errors,
            "noop": self.noop,
            "deleted_plane": self.deleted_plane,
            "deleted_affine": self.deleted_affine,
            "error_messages": self.error_messages[:5],
        }


def _sha(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _parse_affine_datetime(value) -> Optional["object"]:
    """Parse AFFiNE updatedAt (ISO 8601, maybe with Z/offset/ms) -> aware datetime."""
    if not value:
        return None
    if isinstance(value, (int, float)):
        # epoch millis / seconds heuristics
        seconds = value / 1000.0 if value > 1e12 else float(value)
        from datetime import datetime

        try:
            return timezone.make_aware(datetime.fromtimestamp(seconds))
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    # trim sub-microsecond precision Django can't parse
    if "." in normalized:
        head, tail = normalized.split(".", 1)
        frac = "".join(ch for ch in tail if ch.isdigit())
        rest = tail[len(frac):]
        normalized = head + "." + frac[:6].ljust(6, "0") + rest
    from datetime import datetime

    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed)
    return parsed


def _page_hashes(page: Page) -> tuple:
    return _sha(page.description_html or ""), page.updated_at


def _sync_single_page(client, connection, doc_map, doc_meta, stats: SyncStats):
    """Decide and apply the sync action for one mapped pair (own transaction)."""
    plane_page = Page.objects.filter(pk=doc_map.page_id, workspace_id=connection.workspace_id).first()
    if plane_page is None:
        # page hard-deleted upstream; drop the mapping
        doc_map.status = "error"
        doc_map.last_error = "Plane page no longer exists."
        doc_map.save(update_fields=["status", "last_error", "updated_at"])
        stats.errors += 1
        stats.error_messages.append(f"{doc_map.affine_doc_id}: plane page missing")
        return

    try:
        affine_markdown = client.get_doc_markdown(connection.affine_workspace_id, doc_map.affine_doc_id)
    except AffineError as exc:
        doc_map.status = "error"
        doc_map.last_error = str(exc)
        doc_map.save(update_fields=["status", "last_error", "updated_at"])
        stats.errors += 1
        stats.error_messages.append(f"{doc_map.affine_doc_title or doc_map.affine_doc_id}: {exc}")
        return

    affine_changed = _sha(affine_markdown) != doc_map.affine_content_hash
    plane_html = plane_page.description_html or ""
    plane_changed = _sha(plane_html) != doc_map.plane_content_hash

    if not affine_changed and not plane_changed:
        doc_map.status = "synced"
        doc_map.last_sync_direction = "none"
        doc_map.last_synced_at = timezone.now()
        doc_map.save(update_fields=["status", "last_sync_direction", "last_synced_at", "updated_at"])
        stats.noop += 1
        return

    strategy = connection.conflict_strategy
    direction = None
    conflict = False

    if affine_changed and plane_changed:
        conflict = True
        if strategy == "affine_wins":
            direction = "pull"
        elif strategy == "plane_wins":
            direction = "push"
        else:  # newest_wins; an unknowable side counts as old, ties favor AFFiNE
            affine_ts = doc_map.affine_updated_at or _epoch_placeholder()
            plane_ts = doc_map.plane_updated_at or doc_map.last_synced_at or _epoch_placeholder()
            direction = "pull" if affine_ts >= plane_ts else "push"

    elif affine_changed:
        direction = "pull"
    else:
        direction = "push"

    if direction == "pull":
        _apply_pull(client, connection, doc_map, plane_page, affine_markdown, doc_meta, stats, conflict)
    else:
        _apply_push(client, connection, doc_map, plane_page, stats, conflict)


def _apply_pull(client, connection, doc_map, plane_page, affine_markdown, doc_meta, stats: SyncStats, conflict: bool):
    """AFFiNE -> Plane: markdown into description_html (single transaction)."""
    html = markdown_to_html(affine_markdown)
    title = (doc_meta.get("title") if doc_meta else None) or plane_page.name or "Untitled"
    title = title[:MAX_TITLE_LENGTH]
    now = timezone.now()
    with transaction.atomic():
        plane_page.name = title
        plane_page.description_html = html
        plane_page.description_json = {}
        plane_page.save(update_fields=["name", "description_html", "description_json", "description_stripped", "updated_at"])
        # strip_tags side effect runs on save; refresh hash source after save
        doc_map.affine_content_hash = _sha(affine_markdown)
        doc_map.plane_content_hash = _sha(plane_page.description_html or "")
        doc_map.affine_doc_title = title
        if doc_meta and doc_meta.get("updated_at"):
            doc_map.affine_updated_at = doc_meta["updated_at"]
        doc_map.plane_updated_at = plane_page.updated_at
        doc_map.last_synced_at = now
        doc_map.last_sync_direction = "pull"
        doc_map.status = "conflict" if conflict else "synced"
        doc_map.last_error = "Resolved by conflict policy." if conflict else ""
        doc_map.save()
    if conflict:
        stats.conflicts += 1
    else:
        stats.pulled += 1


def _apply_push(client, connection, doc_map, plane_page, stats: SyncStats, conflict: bool):
    """Plane -> AFFiNE: description_html out as markdown (single transaction)."""
    markdown = html_to_markdown(plane_page.description_html or "")
    title = (plane_page.name or "Untitled")[:MAX_TITLE_LENGTH]
    now = timezone.now()
    try:
        client.update_doc(connection.affine_workspace_id, doc_map.affine_doc_id, title, markdown)
    except AffineError as exc:
        doc_map.status = "error"
        doc_map.last_error = str(exc)
        doc_map.save(update_fields=["status", "last_error", "updated_at"])
        stats.errors += 1
        stats.error_messages.append(f"{title}: {exc}")
        return
    with transaction.atomic():
        doc_map.plane_content_hash = _sha(plane_page.description_html or "")
        doc_map.affine_content_hash = _sha(markdown)
        doc_map.affine_doc_title = title
        doc_map.plane_updated_at = plane_page.updated_at
        doc_map.affine_updated_at = now
        doc_map.last_synced_at = now
        doc_map.last_sync_direction = "push"
        doc_map.status = "conflict" if conflict else "synced"
        doc_map.last_error = "Resolved by conflict policy." if conflict else ""
        doc_map.save()
    if conflict:
        stats.conflicts += 1
    else:
        stats.pushed += 1


def _pull_new_plane_pages(client, connection, docs_by_id, existing_doc_ids, stats: SyncStats):
    """Create Plane wiki pages for AFFiNE docs that have no mapping yet."""
    project_exists = Project.objects.filter(pk=connection.project_id, workspace_id=connection.workspace_id).exists()
    if not project_exists:
        stats.errors += 1
        stats.error_messages.append("Connected project no longer exists; cannot create pages.")
        return
    for doc_id, doc_meta in docs_by_id.items():
        if doc_id in existing_doc_ids or doc_meta.get("trash"):
            continue
        try:
            markdown = client.get_doc_markdown(connection.affine_workspace_id, doc_id)
        except AffineError as exc:
            stats.errors += 1
            stats.error_messages.append(f"create page {doc_meta['title']!r}: {exc}")
            continue
        title = (doc_meta["title"] or "Untitled")[:MAX_TITLE_LENGTH]
        html = markdown_to_html(markdown)
        now = timezone.now()
        with transaction.atomic():
            page = Page.objects.create(
                workspace_id=connection.workspace_id,
                owned_by_id=connection.owned_by_id,
                name=title,
                description_html=html,
                description_json={},
                access=0,  # wiki pages mirror shared docs: public within the project
            )
            ProjectPage.objects.create(
                workspace_id=connection.workspace_id,
                project_id=connection.project_id,
                page=page,
            )
            AffinePageMap = _pagemap_model()
            AffinePageMap.objects.create(
                connection=connection,
                page=page,
                affine_doc_id=doc_id,
                affine_doc_title=title,
                affine_content_hash=_sha(markdown),
                plane_content_hash=_sha(page.description_html or ""),
                affine_updated_at=doc_meta.get("updated_at"),
                plane_updated_at=page.updated_at,
                last_synced_at=now,
                last_sync_direction="create",
                status="synced",
            )
        stats.created_plane += 1


def _push_new_affine_docs(client, connection, unmapped_pages, stats: SyncStats):
    """Create AFFiNE docs for connected-project wiki pages with no mapping (opt-in)."""
    AffinePageMap = _pagemap_model()
    for page in unmapped_pages:
        title = (page.name or "Untitled")[:MAX_TITLE_LENGTH]
        markdown = html_to_markdown(page.description_html or "")
        try:
            doc_id = client.create_doc(connection.affine_workspace_id, title, markdown)
        except AffineError as exc:
            stats.errors += 1
            stats.error_messages.append(f"create doc {title!r}: {exc}")
            continue
        now = timezone.now()
        with transaction.atomic():
            AffinePageMap.objects.create(
                connection=connection,
                page=page,
                affine_doc_id=doc_id,
                affine_doc_title=title,
                affine_content_hash=_sha(markdown),
                plane_content_hash=_sha(page.description_html or ""),
                affine_updated_at=now,
                plane_updated_at=page.updated_at,
                last_synced_at=now,
                last_sync_direction="create",
                status="synced",
            )
        stats.created_affine += 1


def _handle_deletions(client, connection, docs_by_id, stats: SyncStats):
    """Surface or propagate deletions per settings.sync_deletions."""
    AffinePageMap = _pagemap_model()
    maps = AffinePageMap.objects.filter(connection=connection).select_related("page")
    for doc_map in maps:
        meta = docs_by_id.get(doc_map.affine_doc_id)
        if meta is None or meta.get("trash"):
            if connection.sync_deletions:
                # remove the Plane page too (soft delete path: archive)
                with transaction.atomic():
                    page = Page.objects.filter(pk=doc_map.page_id).first()
                    if page is not None:
                        page.archived_at = timezone.now().date()
                        page.save(update_fields=["archived_at", "updated_at"])
                    doc_map.status = "error"
                    doc_map.last_error = "Source doc deleted in AFFiNE; page archived."
                    doc_map.save(update_fields=["status", "last_error", "updated_at"])
                stats.deleted_plane += 1
            else:
                doc_map.status = "conflict"
                doc_map.last_error = "Doc missing/trashed in AFFiNE; deletion sync disabled."
                doc_map.save(update_fields=["status", "last_error", "updated_at"])
        elif doc_map.page.archived_at:
            if connection.sync_deletions:
                try:
                    client.delete_doc(connection.affine_workspace_id, doc_map.affine_doc_id)
                    doc_map.status = "error"
                    doc_map.last_error = "Plane page archived; AFFiNE doc trashed."
                    doc_map.save(update_fields=["status", "last_error", "updated_at"])
                    stats.deleted_affine += 1
                except AffineError as exc:
                    doc_map.status = "error"
                    doc_map.last_error = str(exc)
                    doc_map.save(update_fields=["status", "last_error", "updated_at"])
            else:
                doc_map.status = "conflict"
                doc_map.last_error = "Plane page archived; deletion sync disabled."
                doc_map.save(update_fields=["status", "last_error", "updated_at"])


def _pagemap_model():
    from plane.db.models import AffinePageMap

    return AffinePageMap


def _epoch_placeholder():
    """Anchor for rows never synced (treated as infinitely old)."""
    return timezone.now() - timedelta(days=36500)


def run_sync(connection, client: Optional[AffineClient] = None) -> dict:
    """Run one full two-way sync pass for a connection. Returns stats dict."""
    stats = SyncStats()
    now = timezone.now()

    own_client = client is None
    if own_client:
        client = AffineClient(connection.affine_instance_url, connection.api_token)

    try:
        remote_docs = client.list_docs(connection.affine_workspace_id)

        docs_by_id = {}
        for doc in remote_docs:
            entry = {
                "title": doc.get("title") or "",
                "updated_at": _parse_affine_datetime(doc.get("updated_at")),
                "trash": bool(doc.get("trash")),
            }
            docs_by_id[doc["guid"]] = entry

        AffinePageMap = _pagemap_model()

        existing_maps = list(
            AffinePageMap.objects.filter(connection=connection).select_related("page").only(
                "id", "page_id", "affine_doc_id", "affine_doc_title", "affine_content_hash",
                "plane_content_hash", "affine_updated_at", "plane_updated_at",
                "last_synced_at", "status", "connection_id",
            )
        )
        existing_doc_ids = {m.affine_doc_id for m in existing_maps}
        mapped_page_ids = {m.page_id for m in existing_maps}

        # 1) deletions first so a trashed doc never resurrects via create/pull
        _handle_deletions(client, connection, docs_by_id, stats)

        # 2) per-pair three-way decisions (fresh DB state after deletion pass)
        for doc_map in AffinePageMap.objects.filter(connection=connection).select_related("page"):
            doc_meta = docs_by_id.get(doc_map.affine_doc_id)
            if doc_meta is None:
                continue  # deletion pass already flagged it
            _sync_single_page(client, connection, doc_map, doc_meta, stats)

        # 3) create unmapped AFFiNE docs as Plane pages
        _pull_new_plane_pages(client, connection, docs_by_id, existing_doc_ids, stats)

        # 4) opt-in: create unmapped project wiki pages as AFFiNE docs
        if (connection.settings or {}).get("sync_plane_creates"):
            unmapped_pages = list(
                Page.objects.filter(
                    workspace_id=connection.workspace_id,
                    projects__id=connection.project_id,
                    project_pages__deleted_at__isnull=True,
                    archived_at__isnull=True,
                ).exclude(pk__in=mapped_page_ids)[:200]
            )
            _push_new_affine_docs(client, connection, unmapped_pages, stats)

    except AffineError as exc:
        stats.errors += 1
        stats.error_messages.append(str(exc))
    finally:
        if own_client:
            client.close()

    # aggregate bookkeeping for the settings UI
    connection.last_synced_at = now
    if stats.errors:
        connection.last_sync_status = "error"
        connection.last_sync_error = "\n".join(stats.error_messages[:10])
    else:
        connection.last_sync_status = "ok"
        connection.last_sync_error = ""
    connection.save(update_fields=["last_synced_at", "last_sync_status", "last_sync_error", "updated_at"])
    return stats.to_dict()


def verify_and_list_workspaces(instance_url: str, api_token: str) -> list:
    """Connect-time probe used by the settings UI before saving a connection."""
    with AffineClient(instance_url, api_token) as client:
        return client.list_workspaces()
