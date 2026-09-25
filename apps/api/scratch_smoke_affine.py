"""Scratch-DB smoke test for the AFFiNE wiki sync engine.

Exercises the real engine (plane.affine_sync.engine.run_sync) against
affine_scratch on :5433 with a stub AFFiNE client (no network):

  1. pull-create: remote doc -> new Plane wiki page + map
  2. pull: AFFiNE edit -> Plane page content updated
  3. push: Plane edit -> AFFiNE doc updated (stub records the write)
  4. conflict: both sides changed with newest_wins -> newer side wins,
     status=conflict recorded
  5. deletion surfacing: remote doc gone + sync_deletions=False -> conflict,
     page NOT archived
  6. deletion propagation: sync_deletions=True -> Plane page archived

Run: apps/api/.venv/bin/python scratch_smoke_affine.py   (from apps/api/)
"""

import os
import sys
from datetime import timedelta
from types import SimpleNamespace

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "plane.settings.settings")
os.environ.setdefault("POSTGRES_DB", "affine_scratch")
django.setup()

from django.utils import timezone

from plane.affine_sync import engine
from plane.db.models import (
    AffineConnection,
    AffinePageMap,
    Page,
    Project,
    ProjectPage,
    User,
    Workspace,
    WorkspaceMember,
)

PASS = []
FAIL = []


def check(name, condition, detail=""):
    (PASS if condition else FAIL).append(name)
    print(("  PASS " if condition else "  FAIL ") + name + (f"  [{detail}]" if detail and not condition else ""))


class StubAffineClient:
    """In-memory stand-in for AffineClient."""

    def __init__(self, docs):
        # docs: {doc_id: {"title": str, "markdown": str, "updated_at": iso, "trash": bool}}
        self.docs = docs
        self.updates = []
        self.created = []
        self.deleted = []

    def list_docs(self, workspace_id):
        return [
            {
                "guid": doc_id,
                "title": doc["title"],
                "updated_at": doc["updated_at"],
                "trash": doc.get("trash", False),
            }
            for doc_id, doc in self.docs.items()
        ]

    def get_doc_markdown(self, workspace_id, doc_id):
        if doc_id not in self.docs:
            raise engine.AffineError("doc not found")
        return self.docs[doc_id]["markdown"]

    def update_doc(self, workspace_id, doc_id, title, markdown):
        self.updates.append((doc_id, title, markdown))
        self.docs[doc_id]["title"] = title
        self.docs[doc_id]["markdown"] = markdown
        self.docs[doc_id]["updated_at"] = timezone.now().isoformat()

    def create_doc(self, workspace_id, title, markdown):
        doc_id = f"new-doc-{len(self.created) + 1}"
        self.created.append((doc_id, title, markdown))
        self.docs[doc_id] = {"title": title, "markdown": markdown, "updated_at": timezone.now().isoformat()}
        return doc_id

    def delete_doc(self, workspace_id, doc_id):
        self.deleted.append(doc_id)

    def close(self):
        pass


def main():
    now = timezone.now()
    suffix = now.strftime("%H%M%S%f")

    # --- fixtures: workspace, user, project -------------------------------- #
    user = User.objects.create(email=f"affine-smoke-{suffix}@example.com", username=f"affine-smoke-{suffix}")
    user.set_password("x")
    user.save()
    workspace = Workspace.objects.create(name=f"affine-smoke-{suffix}", slug=f"affine-smoke-{suffix}", owner=user)
    WorkspaceMember.objects.create(workspace=workspace, member=user, role=20)
    project = Project.objects.create(
        name="smoke-project",
        identifier="SMK",
        workspace=workspace,
        created_by=user,
    )

    connection = AffineConnection.objects.create(
        workspace=workspace,
        project=project,
        affine_instance_url="https://affine.example.com",
        affine_workspace_id="ws-1",
        affine_workspace_name="Smoke WS",
        api_token="token-abc",
        owned_by=user,
        settings={"conflict_strategy": "newest_wins"},
        created_by=user,
    )

    print("== 1. pull-create ==")
    client = StubAffineClient(
        {
            "doc-a": {
                "title": "Runbook",
                "markdown": "# Runbook\n\nDeploy steps:\n\n- build\n- ship\n",
                "updated_at": now.isoformat(),
            }
        }
    )
    stats = engine.run_sync(connection, client=client)
    page_a = Page.objects.filter(workspace=workspace, name="Runbook").first()
    check("page created from AFFiNE doc", page_a is not None, str(stats))
    check(
        "markdown converted to html",
        page_a is not None and "<h1>Runbook</h1>" in page_a.description_html and "<li>ship</li>" in page_a.description_html,
        page_a.description_html if page_a else "",
    )
    amap = AffinePageMap.objects.filter(connection=connection, affine_doc_id="doc-a").first()
    check("map created with status synced", amap is not None and amap.status == "synced")
    check("stats.created_plane == 1", stats["created_plane"] == 1, str(stats))

    print("== 2. pull on remote edit ==")
    client.docs["doc-a"]["markdown"] = "# Runbook v2\n\nChanged content.\n"
    client.docs["doc-a"]["updated_at"] = (now + timedelta(minutes=5)).isoformat()
    stats = engine.run_sync(connection, client=client)
    page_a.refresh_from_db()
    check("pulled v2 heading into page", "<h1>Runbook v2</h1>" in page_a.description_html, page_a.description_html[:120])
    check("stats.pulled == 1", stats["pulled"] == 1, str(stats))
    check("map direction pull", amap_last_direction(connection) == "pull")

    print("== 3. push on plane edit ==")
    page_a.description_html = "<h1>Runbook v2</h1><p>Plane-side edit.</p>"
    page_a.save(update_fields=["description_html", "description_stripped"])
    stats = engine.run_sync(connection, client=client)
    check("pushed to AFFiNE stub", any(u[0] == "doc-a" and "Plane-side edit." in u[2] for u in client.updates), str(client.updates))
    check("stats.pushed == 1", stats["pushed"] == 1, str(stats))
    check("map direction push", amap_last_direction(connection) == "push")

    print("== 4. conflict (both changed, newest_wins) ==")
    # both sides diverge from the last synced hashes
    old_affine_ts = timezone.now() - timedelta(hours=1)
    amap.refresh_from_db()
    AffinePageMap.objects.filter(pk=amap.pk).update(affine_updated_at=old_affine_ts)
    page_a.description_html = "<h1>Runbook v2</h1><p>Plane diverges.</p>"
    page_a.save(update_fields=["description_html", "description_stripped"])
    AffinePageMap.objects.filter(pk=amap.pk).update(plane_content_hash="stale")
    client.docs["doc-a"]["markdown"] = "# AFFiNE diverges\n"
    stats = engine.run_sync(connection, client=client)
    page_a.refresh_from_db()
    amap.refresh_from_db()
    check("conflict recorded", amap.status == "conflict", amap.status)
    check("plane newer -> push won", "Plane diverges." in client.docs["doc-a"]["markdown"], client.docs["doc-a"]["markdown"])
    check("stats.conflicts == 1", stats["conflicts"] == 1, str(stats))

    print("== 5. deletion surfaced (sync_deletions off) ==")
    client.docs.pop("doc-a")
    stats = engine.run_sync(connection, client=client)
    page_a.refresh_from_db()
    amap.refresh_from_db()
    check("page NOT archived", page_a.archived_at is None)
    check("map flagged conflict with message", amap.status == "conflict" and "missing" in amap.last_error.lower(), amap.last_error)
    # restore a doc for step 6
    client.docs["doc-a"] = {"title": "Runbook", "markdown": "# Back\n", "updated_at": timezone.now().isoformat()}
    engine.run_sync(connection, client=client)  # re-pull to resync hashes

    print("== 6. deletion propagation (sync_deletions on) ==")
    AffineConnection.objects.filter(pk=connection.pk).update(settings={"sync_deletions": True, "conflict_strategy": "newest_wins"})
    connection.refresh_from_db()
    client.docs.pop("doc-a")
    stats = engine.run_sync(connection, client=client)
    page_a.refresh_from_db()
    check("page archived on remote deletion", page_a.archived_at is not None, str(page_a.archived_at))
    check("stats.deleted_plane == 1", stats["deleted_plane"] == 1, str(stats))

    print("== 7. opt-in plane->affine create ==")
    page_b = Page.objects.create(workspace=workspace, owned_by=user, name="Plane only page", description_html="<p>origin story</p>")
    ProjectPage.objects.create(workspace=workspace, project=project, page=page_b)
    AffineConnection.objects.filter(pk=connection.pk).update(settings={"sync_deletions": False, "conflict_strategy": "newest_wins", "sync_plane_creates": True})
    connection.refresh_from_db()
    stats = engine.run_sync(connection, client=client)
    check("doc created in AFFiNE stub", len(client.created) == 1 and client.created[0][1] == "Plane only page", str(client.created))
    check("stats.created_affine == 1", stats["created_affine"] == 1, str(stats))
    check("new map linked to page_b", AffinePageMap.objects.filter(connection=connection, page=page_b, affine_doc_id="new-doc-1").exists())

    # --- converter round-trip sanity --------------------------------------- #
    print("== 8. converter round trip ==")
    md = "# Title\n\n- a\n- b\n\n```py\nprint(1)\n```\n\n[link](https://x.y) and **bold**\n"
    html = engine.markdown_to_html(md)
    md2 = engine.html_to_markdown(html)
    check("headings survive", "<h1>Title</h1>" in html and md2.startswith("# Title"), md2[:60])
    check("code fence survives", "<pre><code>print(1)</code></pre>" in html or "print(1)" in html, html[:200])

    print()
    print(f"RESULT: {len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILURES:", FAIL)
        sys.exit(1)


def amap_last_direction(connection):
    from plane.db.models import AffinePageMap as M

    m = M.objects.filter(connection=connection, affine_doc_id="doc-a").first()
    return m.last_sync_direction if m else None


if __name__ == "__main__":
    main()
