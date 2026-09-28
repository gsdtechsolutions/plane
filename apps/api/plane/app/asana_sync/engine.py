# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Asana <-> Plane bidirectional sync engine.

One `AsanaSyncEngine` instance runs a single pass over one `AsanaProjectSync`.

Core rules
----------
- Identity: `AsanaTaskLink` rows are the only bridge; tasks without links are
  created (pull) or pushed (push direction), never guessed by name.
- Loop prevention: after either direction writes, both cached timestamps
  (`asana_modified_at`, `plane_synced_at`) are aligned to the written content,
  so the mirror pass sees "nothing changed" and stops.
- Conflicts: if both sides changed since the last pass, the newer
  modification timestamp wins (last-write-wins) and the loser is recorded in
  `AsanaSyncLog` with status "conflict".
- Lazy provisioning: unmapped Asana sections/tags get a Plane state/label
  created on first sight and are registered back into the sync's maps. Unmapped
  Asana assignees get a person-label (named after the Asana user) so they stay
  visible in Plane; member: mappings assign the real Plane member instead.
- Echo suppression: pull-side writes run inside suppress_asana_sync() so the
  push signal receivers (signals.py) never mirror them back to Asana.
"""

# Python imports
import json
import logging
import traceback
from datetime import timedelta
from typing import Any, Optional
from uuid import UUID

# Django imports
from django.db import transaction
from django.utils import timezone

# Module imports
from plane.app.asana_sync import mapping
from plane.app.asana_sync.client import AsanaAPIError, AsanaAuthError, AsanaClient
from plane.app.asana_sync.signals import suppress_asana_sync
from plane.bgtasks.issue_activities_task import issue_activity
from plane.db.models import (
    AsanaCommentLink,
    AsanaProjectSync,
    AsanaSyncLog,
    AsanaTaskLink,
    Issue,
    IssueAssignee,
    IssueComment,
    IssueLabel,
    Label,
    ProjectMember,
    State,
)
from plane.utils.exception_logger import log_exception

logger = logging.getLogger(__name__)

PULL_WINDOW_DAYS = 30
LOG_RETENTION_DAYS = 30


class AsanaSyncEngine:
    def __init__(self, sync: AsanaProjectSync, client: AsanaClient):
        self.sync = sync
        self.client = client
        self._states_by_id: dict[str, State] = {}
        self._labels_by_id: dict[str, Label] = {}
        self._members: set[str] = set()
        self._section_names: Optional[dict[str, str]] = None

    def _section_name_lookup(self) -> dict[str, str]:
        """gid -> section name, fetched once per pass for provisioning."""
        if self._section_names is None:
            try:
                self._section_names = {
                    s["gid"]: (s.get("name") or "Asana section")
                    for s in self.client.sections(self.sync.asana_project_gid)
                }
            except AsanaAPIError:
                self._section_names = {}
        return self._section_names

    # ------------------------------------------------------------------ helpers

    def _log(
        self,
        direction: str,
        entity_type: str,
        entity_gid: str = "",
        issue_id: Optional[str] = None,
        status: str = "success",
        message: str = "",
        detail: Optional[dict] = None,
    ) -> None:
        try:
            AsanaSyncLog.objects.create(
                project=self.sync.project,
                sync=self.sync,
                direction=direction,
                entity_type=entity_type,
                entity_gid=entity_gid or "",
                issue_id=issue_id,
                status=status,
                message=message[:512],
                detail=detail or {},
            )
        except Exception:
            log_exception(traceback.format_exc())

    def _state(self, state_id) -> Optional[State]:
        key = str(state_id)
        if key not in self._states_by_id:
            self._states_by_id[key] = State.objects.filter(
                id=state_id, project_id=self.sync.project_id
            ).first()
        return self._states_by_id.get(key)

    def _label(self, label_id) -> Optional[Label]:
        key = str(label_id)
        if key not in self._labels_by_id:
            self._labels_by_id[key] = Label.objects.filter(
                id=label_id, project_id=self.sync.project_id
            ).first()
        return self._labels_by_id.get(key)

    def _known_member_ids(self) -> set[str]:
        if not self._members:
            self._members = set(
                str(mid)
                for mid in ProjectMember.objects.filter(
                    project_id=self.sync.project_id, is_active=True
                ).values_list("member_id", flat=True)
            )
        return self._members

    def _set_assignees(self, issue: Issue, assignee_ids) -> None:
        """Replace assignees via IssueAssignee through rows — plain
        assignees.set() fails because the through row carries NOT NULL
        project/workspace columns (see github_delivery._assign_issue)."""
        issue.assignees.clear()
        valid = list(dict.fromkeys(str(a) for a in (assignee_ids or []) if str(a) in self._known_member_ids()))
        if valid:
            IssueAssignee.objects.bulk_create(
                IssueAssignee(
                    assignee_id=mid,
                    issue=issue,
                    project_id=issue.project_id,
                    workspace_id=issue.workspace_id,
                )
                for mid in valid
            )

    def _set_labels(self, issue: Issue, label_ids) -> None:
        """Replace labels via IssueLabel through rows (same NOT NULL constraint)."""
        issue.labels.clear()
        valid = list(dict.fromkeys(str(l) for l in (label_ids or []) if self._label(l)))
        if valid:
            IssueLabel.objects.bulk_create(
                IssueLabel(
                    label_id=lid,
                    issue=issue,
                    project_id=issue.project_id,
                    workspace_id=issue.workspace_id,
                )
                for lid in valid
            )

    def _first_completed_state_id(self) -> Optional[str]:
        state = (
            State.objects.filter(
                project_id=self.sync.project_id, group__in=["completed", "cancelled"]
            )
            .order_by("sequence")
            .first()
        )
        return str(state.id) if state else None

    def _ensure_state_for_section(self, section_gid: str, section_name: Optional[str]) -> Optional[str]:
        """Resolve a section to a state id, lazily creating a state for unknown sections."""
        state_map = dict(self.sync.state_map or {})
        entry = state_map.get(section_gid)
        if entry and entry.get("state_id") and self._state(entry["state_id"]):
            return str(entry["state_id"])

        state, created = State.objects.get_or_create(
            project_id=self.sync.project_id,
            name=(section_name or "Asana section").strip()[:255],
            defaults={
                "color": "#60646C",
                "group": "unstarted",
                "sequence": 25000,
                "description": f"Synced from Asana section {section_gid}",
            },
        )
        state_map[section_gid] = {"state_id": str(state.id), "name": state.name}
        self.sync.state_map = state_map
        self.sync.save(update_fields=["state_map", "updated_at"])
        if created:
            self._log("pull", "project", self.sync.asana_project_gid, status="success",
                      message=f"Provisioned state '{state.name}' for Asana section")
        return str(state.id)

    def _ensure_label_for_tag(self, tag_gid: str, tag_name: Optional[str]) -> Optional[str]:
        """Resolve an Asana tag to a label id, lazily creating a label for unknown tags."""
        label_map = dict(self.sync.label_map or {})
        label_id = label_map.get(tag_gid)
        if label_id and self._label(label_id):
            return str(label_id)

        label, created = Label.objects.get_or_create(
            project_id=self.sync.project_id,
            name=(tag_name or f"asana-{tag_gid[:8]}").strip()[:255],
            defaults={"color": "#3f76ff", "sort_order": 65535},
        )
        label_map[tag_gid] = str(label.id)
        self.sync.label_map = label_map
        self.sync.save(update_fields=["label_map", "updated_at"])
        if created:
            self._log("pull", "project", tag_gid, status="success",
                      message=f"Provisioned label '{label.name}' for Asana tag")
        return str(label.id)

    def _resolve_state_for_task(self, task: dict) -> Optional[str]:
        section_gid = mapping.section_gid_of(task)
        state_id: Optional[str] = None
        if section_gid:
            entry = (self.sync.state_map or {}).get(section_gid) or {}
            section_name = entry.get("name") or self._section_name_lookup().get(section_gid)
            state_id = self._ensure_state_for_section(section_gid, section_name)
        if not state_id:
            state_id = str(self.sync.default_state_id) if self.sync.default_state_id else None
        if not state_id:
            default_state = State.objects.filter(
                project_id=self.sync.project_id, default=True
            ).first()
            state_id = str(default_state.id) if default_state else None

        # Completed Asana tasks land in a completed-group state, not just "Done" naming.
        if task.get("completed") and state_id:
            state = self._state(state_id)
            if state and state.group not in ("completed", "cancelled"):
                completed_state_id = self._first_completed_state_id()
                if completed_state_id:
                    state_id = completed_state_id
        return state_id

    def _assignee_gid_to_plane(self, task: dict) -> Optional[str]:
        """Asana assignee -> Plane member id (member: map values only)."""
        assignee = task.get("assignee")
        gid = assignee.get("gid") if isinstance(assignee, dict) else assignee
        if not gid:
            return None
        member_id = mapping.assignee_value_member_id((self.sync.assignee_map or {}).get(gid))
        if member_id and str(member_id) in self._known_member_ids():
            return str(member_id)
        return None

    def _assignee_plane_to_gid(self, issue: Issue) -> Optional[str]:
        member = issue.assignees.first()
        if member is None:
            return None
        for gid, raw in (self.sync.assignee_map or {}).items():
            member_id = mapping.assignee_value_member_id(raw)
            if member_id and str(member_id) == str(member.id):
                return gid
        return None

    def _ensure_person_label(self, assignee_gid: str, assignee_name: Optional[str]) -> Optional[str]:
        """Resolve an unmapped Asana assignee to a person-label id.

        Asana-only users (no Plane member mapping) become project labels named
        after the Asana user so they stay visible and filterable in Plane; the
        mapping is registered back as "label:<id>". An explicit "" value
        suppresses provisioning for that gid entirely.
        """
        raw = (self.sync.assignee_map or {}).get(assignee_gid)
        if raw is not None and str(raw) == "":
            return None  # admin disabled this person
        label_id = mapping.assignee_value_label_id(raw)
        if label_id and self._label(label_id):
            return str(label_id)
        if mapping.assignee_value_member_id(raw):
            return None  # mapped to a real member; no label wanted

        label, created = Label.objects.get_or_create(
            project_id=self.sync.project_id,
            name=(assignee_name or f"Asana user {assignee_gid[:8]}").strip()[:255],
            defaults={"color": "#F06A6A", "sort_order": 65535},
        )
        assignee_map = dict(self.sync.assignee_map or {})
        assignee_map[assignee_gid] = f"{mapping.ASSIGNEE_LABEL_PREFIX}{label.id}"
        self.sync.assignee_map = assignee_map
        self.sync.save(update_fields=["assignee_map", "updated_at"])
        if created:
            self._log("pull", "project", assignee_gid, status="success",
                      message=f"Provisioned person label '{label.name}' for Asana assignee")
        return str(label.id)

    # ------------------------------------------------------------------ pull

    def pull_full(self) -> int:
        """Full/delta pass: tasks changed since last_synced_at (first run: everything)."""
        if not self.sync.connection.is_active or not self.sync.is_active:
            return 0
        since = None
        if self.sync.initial_sync_done and self.sync.last_synced_at:
            since = (self.sync.last_synced_at - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%S.000Z")

        try:
            remote_tasks = self.client.tasks(self.sync.asana_project_gid, modified_since=since)
        except AsanaAuthError:
            self._log("pull", "project", self.sync.asana_project_gid, status="error",
                      message="Asana rejected credentials — reconnect required")
            return 0
        except AsanaAPIError as exc:
            self._log("pull", "project", self.sync.asana_project_gid, status="error",
                      message=f"Failed to list Asana tasks: {exc}")
            return 0

        parents = [t for t in remote_tasks if not t.get("parent")]
        subtasks = [t for t in remote_tasks if t.get("parent")]
        processed = 0
        for task in parents:
            if self._pull_task(task):
                processed += 1
        if self.sync.sync_subtasks:
            for task in subtasks:
                if self._pull_task(task):
                    processed += 1

        self.sync.initial_sync_done = True
        self.sync.last_synced_at = timezone.now()
        self.sync.save(update_fields=["initial_sync_done", "last_synced_at", "updated_at"])
        return processed

    def pull_task_by_gid(self, task_gid: str) -> bool:
        """Webhook-triggered single-task pull."""
        if not self.sync.connection.is_active or not self.sync.is_active:
            return False
        try:
            task = self.client.task(task_gid)
        except AsanaAPIError as exc:
            self._log("pull", "task", task_gid, status="error", message=f"Fetch failed: {exc}")
            return False
        # Webhook fires for tasks that merely touched the project; if it moved out, ignore.
        if not self._task_in_project(task):
            return False
        return self._pull_task(task)

    def _task_in_project(self, task: dict) -> bool:
        for membership in task.get("memberships") or []:
            project = membership.get("project")
            gid = project.get("gid") if isinstance(project, dict) else project
            if gid == self.sync.asana_project_gid:
                return True
        return False

    def _pull_task(self, task: dict) -> bool:
        task_gid = task.get("gid")
        if not task_gid:
            return False
        try:
            link = AsanaTaskLink.objects.filter(
                sync=self.sync, asana_task_gid=task_gid, deleted_at__isnull=True
            ).first()

            remote_modified = mapping.asana_datetime(task.get("modified_at"))
            if link and remote_modified and link.asana_modified_at and remote_modified <= link.asana_modified_at:
                local_changed = link.issue_id and link.plane_synced_at and link.issue.updated_at and link.issue.updated_at > link.plane_synced_at
                if not local_changed or self.sync.direction == "pull":
                    return False

            # Concurrently-changed local issue wins if it is newer (LWW).
            if link and self.sync.direction != "push":
                issue = link.issue
                if (
                    issue
                    and link.plane_synced_at
                    and issue.updated_at
                    and issue.updated_at > link.plane_synced_at
                    and (not remote_modified or not link.asana_modified_at or issue.updated_at >= link.asana_modified_at)
                ):
                    self._log("pull", "task", task_gid, str(link.issue_id), status="conflict",
                              message="Local issue is newer; remote change superseded (LWW)")
                    self._push_linked_issue(link, task)
                    return True

            if self.sync.direction == "push":
                if link:
                    self._push_linked_issue(link, task)
                return False

            parent_gid = None
            parent_task = task.get("parent") if isinstance(task.get("parent"), dict) else None
            if parent_task:
                parent_gid = parent_task.get("gid")
            parent_issue_id = None
            if parent_gid:
                parent_link = AsanaTaskLink.objects.filter(
                    sync=self.sync, asana_task_gid=parent_gid, deleted_at__isnull=True
                ).first()
                parent_issue_id = str(parent_link.issue_id) if parent_link else None

            fields = mapping.build_issue_fields_from_task(
                task,
                state_id=self._resolve_state_for_task(task),
                assignee_map=self.sync.assignee_map or {},
                label_map=self.sync.label_map or {},
                known_tag_ids=set(),
                parent_issue_id=parent_issue_id,
            )
            fields.pop("label_ids", None)
            tag_label_ids: list[str] = []
            for tag in task.get("tags") or []:
                tag_gid = tag.get("gid") if isinstance(tag, dict) else tag
                tag_name = tag.get("name") if isinstance(tag, dict) else None
                if tag_gid:
                    label_id = self._ensure_label_for_tag(tag_gid, tag_name)
                    if label_id:
                        tag_label_ids.append(label_id)

            # Person label for the Asana assignee (Asana-only users without a
            # member mapping stay visible as labels).
            person_label_id: Optional[str] = None
            assignee = task.get("assignee")
            assignee_gid = assignee.get("gid") if isinstance(assignee, dict) else assignee
            if assignee_gid and not self._assignee_gid_to_plane(task):
                person_label_id = self._ensure_person_label(
                    str(assignee_gid),
                    assignee.get("name") if isinstance(assignee, dict) else None,
                )
            desired = list(tag_label_ids)
            if person_label_id:
                desired.append(person_label_id)

            if link is None:
                if desired:
                    fields["label_ids"] = desired
            else:
                # Sync-managed labels (tag mappings + person labels) are
                # recomputed from Asana on every pull; labels outside the
                # sync's maps are never touched.
                managed = {str(v) for v in (self.sync.label_map or {}).values()}
                for raw in (self.sync.assignee_map or {}).values():
                    pid = mapping.assignee_value_label_id(raw)
                    if pid:
                        managed.add(str(pid))
                current_ids = [str(l) for l in link.issue.labels.values_list("id", flat=True)]
                kept = [x for x in current_ids if x not in managed]
                seen: set[str] = set()
                final = [x for x in kept + desired if not (x in seen or seen.add(x))]
                if final != current_ids:
                    fields["label_ids"] = final

            if link is None:
                issue = self._create_issue(fields, actor_id=self.sync.project.created_by_id)
                link = AsanaTaskLink.objects.create(
                    project=self.sync.project,
                    sync=self.sync,
                    issue=issue,
                    asana_task_gid=task_gid,
                    asana_modified_at=remote_modified,
                    asana_name_hash=mapping.content_hash(task.get("name") or ""),
                )
                self._log("pull", "task", task_gid, str(issue.id), message="Created issue from Asana task")
            else:
                issue = self._update_issue(link.issue, fields, actor_id=self.sync.project.created_by_id)
                link.asana_modified_at = remote_modified
                link.asana_name_hash = mapping.content_hash(task.get("name") or "")
                self._log("pull", "task", task_gid, str(issue.id), message="Updated issue from Asana task")

            # Align loop-guard timestamps after the local write.
            issue.refresh_from_db(fields=["updated_at"])
            link.plane_synced_at = issue.updated_at
            link.save(update_fields=["asana_modified_at", "plane_synced_at", "asana_name_hash", "updated_at"])

            if self.sync.sync_comments:
                self._pull_comments(link, task_gid)
            return True
        except AsanaAuthError:
            self._log("pull", "task", task_gid, status="error", message="Asana rejected credentials")
            return False
        except Exception as exc:
            log_exception(traceback.format_exc())
            self._log("pull", "task", task_gid, status="error", message=f"Pull failed: {exc}")
            return False

    # ------------------------------------------------------------------ push

    def push_full(self) -> int:
        """Push Plane issues (created/changed since last pass) that have no/changed link."""
        if not self.sync.connection.is_active or not self.sync.is_active:
            return 0
        if self.sync.direction == "pull":
            return 0

        queryset = Issue.objects.filter(
            project_id=self.sync.project_id, archived_at__isnull=True, is_draft=False
        ).select_related("state").prefetch_related("assignees", "labels")
        if self.sync.last_synced_at:
            queryset = queryset.filter(updated_at__gt=self.sync.last_synced_at)

        pushed = 0
        for issue in queryset.order_by("created_at")[:500]:
            try:
                if self.push_issue(issue):
                    pushed += 1
            except AsanaAuthError:
                self._log("push", "task", issue_id=str(issue.id), status="error",
                          message="Asana rejected credentials")
                break
            except Exception as exc:
                log_exception(traceback.format_exc())
                self._log("push", "task", issue_id=str(issue.id), status="error", message=f"Push failed: {exc}")

        self.sync.initial_sync_done = True
        self.sync.last_synced_at = timezone.now()
        self.sync.save(update_fields=["initial_sync_done", "last_synced_at", "updated_at"])
        return pushed

    def push_issue(self, issue: Issue) -> bool:
        if issue.is_draft or issue.archived_at:
            return False
        link = AsanaTaskLink.objects.filter(
            sync=self.sync, issue=issue, deleted_at__isnull=True
        ).select_related("issue").first()
        if link:
            # Delta guard: skip when nothing changed since the last aligned push.
            if link.plane_synced_at and issue.updated_at and issue.updated_at <= link.plane_synced_at:
                return False
            return self._push_linked_issue(link)
        if self.sync.direction == "pull":
            return False
        return self._push_new_issue(issue)

    def _push_new_issue(self, issue: Issue) -> bool:
        payload = self._task_payload(issue)
        try:
            task = self.client.create_task(self.sync.connection.asana_workspace_gid, payload)
            self.client.add_task_to_project(task["gid"], self.sync.asana_project_gid)
            section_gid = self._section_for_state(issue)
            if section_gid:
                self.client.move_task_to_section(task["gid"], section_gid)
        except AsanaAPIError as exc:
            self._log("push", "task", str(issue.id), str(issue.id), status="error", message=f"Asana create failed: {exc}")
            return False

        link = AsanaTaskLink.objects.create(
            project=self.sync.project,
            sync=self.sync,
            issue=issue,
            asana_task_gid=task["gid"],
            asana_modified_at=mapping.asana_datetime(task.get("modified_at")),
        )
        issue.refresh_from_db(fields=["updated_at"])
        link.plane_synced_at = issue.updated_at
        link.save(update_fields=["plane_synced_at", "updated_at"])
        self._log("push", "task", task["gid"], str(issue.id), message="Created Asana task from issue")
        if self.sync.sync_comments:
            self._push_comments(link)
        return True

    def _push_linked_issue(self, link: AsanaTaskLink, remote_task: Optional[dict] = None) -> bool:
        issue = link.issue
        if issue is None or issue.deleted_at is not None:
            return False
        payload = self._task_payload(issue)
        try:
            task = self.client.update_task(link.asana_task_gid, payload)
            section_gid = self._section_for_state(issue)
            if section_gid:
                self.client.move_task_to_section(link.asana_task_gid, section_gid)
        except AsanaAPIError as exc:
            status_code = getattr(exc, "status_code", None)
            if status_code == 404:
                # Remote task deleted — break the link so the next pass re-creates it.
                link.delete()
                self._log("push", "task", link.asana_task_gid, str(issue.id), status="skipped",
                          message="Asana task gone; link removed (re-creates on next pass)")
                return False
            self._log("push", "task", link.asana_task_gid, str(issue.id), status="error",
                      message=f"Asana update failed: {exc}")
            return False

        link.asana_modified_at = mapping.asana_datetime(task.get("modified_at"))
        issue.refresh_from_db(fields=["updated_at"])
        link.plane_synced_at = issue.updated_at
        link.save(update_fields=["asana_modified_at", "plane_synced_at", "updated_at"])
        self._log("push", "task", link.asana_task_gid, str(issue.id), message="Updated Asana task from issue")
        if self.sync.sync_comments:
            self._push_comments(link)
        return True

    def _task_payload(self, issue: Issue) -> dict:
        description = issue.description_html or "<p></p>"
        completed = False
        if issue.state and issue.state.group in ("completed", "cancelled"):
            completed = True
        elif issue.completed_at:
            completed = True
        tag_gids: list[str] = []
        for label_id in issue.labels.values_list("id", flat=True):
            for gid, mapped in (self.sync.label_map or {}).items():
                if str(mapped) == str(label_id):
                    tag_gids.append(gid)
                    break
        assignee_gid = self._assignee_plane_to_gid(issue)
        if assignee_gid is None:
            # Person-label fallback: an issue carrying the person label of an
            # Asana-only user pushes the assignment back to Asana.
            issue_label_ids = {str(l) for l in issue.labels.values_list("id", flat=True)}
            for gid, raw in (self.sync.assignee_map or {}).items():
                pid = mapping.assignee_value_label_id(raw)
                if pid and str(pid) in issue_label_ids:
                    assignee_gid = gid
                    break
        return mapping.build_task_payload_from_issue(
            issue,
            description_html=description,
            completed=completed,
            assignee_gid=assignee_gid,
            tag_gids=tag_gids,
        )

    def _section_for_state(self, issue: Issue) -> Optional[str]:
        state_id = str(issue.state_id) if issue.state_id else None
        if not state_id:
            return None
        for section_gid, entry in (self.sync.state_map or {}).items():
            if str(entry.get("state_id")) == state_id:
                return section_gid
        return None

    # ------------------------------------------------------------------ comments

    def _pull_comments(self, link: AsanaTaskLink, task_gid: str) -> None:
        try:
            stories = self.client.stories(task_gid)
        except AsanaAPIError as exc:
            self._log("pull", "comment", task_gid, str(link.issue_id), status="error",
                      message=f"Story fetch failed: {exc}")
            return
        for story in stories:
            if story.get("resource_subtype") != "comment_added":
                continue
            story_gid = story.get("gid")
            if not story_gid:
                continue
            exists = AsanaCommentLink.objects.filter(
                task_link=link, asana_story_gid=story_gid, deleted_at__isnull=True
            ).exists()
            if exists:
                continue
            comment_html = mapping.asana_story_to_comment_html(story.get("html_text") or story.get("text") or "")
            # Suppressed: mirrored writes must not re-trigger the push signals.
            with suppress_asana_sync(), transaction.atomic():
                comment = IssueComment.objects.create(
                    project=self.sync.project,
                    issue=link.issue,
                    actor=None,
                    comment_html=comment_html,
                    access="EXTERNAL",
                    external_source=f"asana:{self.sync.id}",
                    external_id=story_gid,
                )
                AsanaCommentLink.objects.create(
                    project=self.sync.project,
                    task_link=link,
                    issue_comment=comment,
                    asana_story_gid=story_gid,
                    direction="pull",
                )
            self._log("pull", "comment", story_gid, str(link.issue_id), message="Pulled Asana comment")

    def _push_comments(self, link: AsanaTaskLink) -> None:
        comments = (
            IssueComment.objects.filter(issue=link.issue, deleted_at__isnull=True)
            .exclude(external_source__startswith="asana:")
            .order_by("created_at")
        )
        linked_comment_ids = set(
            AsanaCommentLink.objects.filter(task_link=link, issue_comment__isnull=False, deleted_at__isnull=True)
            .values_list("issue_comment_id", flat=True)
        )
        for comment in comments:
            if comment.id in linked_comment_ids:
                continue
            text = mapping.comment_text_for_asana(comment.comment_html)
            if not text:
                continue
            try:
                story = self.client.create_story(link.asana_task_gid, text)
            except AsanaAPIError as exc:
                self._log("push", "comment", str(comment.id), str(link.issue_id), status="error",
                          message=f"Asana story create failed: {exc}")
                continue
            AsanaCommentLink.objects.create(
                project=self.sync.project,
                task_link=link,
                issue_comment=comment,
                asana_story_gid=story.get("gid", ""),
                direction="push",
            )
            self._log("push", "comment", story.get("gid", ""), str(link.issue_id), message="Pushed comment to Asana")

    # ------------------------------------------------------------------ issue write helpers

    def _issue_activity(self, activity_type: str, current_instance: dict, actor_id: Optional[str], issue: Issue) -> None:
        """Mirror the bgtasks/issue_automation_task idiom; needs a real actor to attribute."""
        if not actor_id:
            return
        issue_activity.delay(
            type=activity_type,
            requested_data=json.dumps({"automation": True, "source": "asana_sync"}),
            actor_id=str(actor_id),
            issue_id=str(issue.id),
            project_id=str(self.sync.project_id),
            current_instance=json.dumps(current_instance),
            subscriber=False,
            epoch=int(timezone.now().timestamp()),
            notification=True,
        )

    def _create_issue(self, fields: dict, actor_id: Optional[str]) -> Issue:
        # Suppressed: mirrored writes must not re-trigger the push signals.
        with suppress_asana_sync(), transaction.atomic():
            issue = Issue.objects.create(
                project=self.sync.project,
                **{k: v for k, v in fields.items() if k not in ("assignee_ids", "label_ids")},
            )
            if fields.get("assignee_ids"):
                self._set_assignees(issue, fields["assignee_ids"])
            if fields.get("label_ids"):
                self._set_labels(issue, fields["label_ids"])
            self._issue_activity("issue.activity.created", {}, actor_id, issue)
        return issue

    def _update_issue(self, issue: Issue, fields: dict, actor_id: Optional[str]) -> Issue:
        current = {
            "name": issue.name,
            "description_html": issue.description_html,
            "state_id": str(issue.state_id) if issue.state_id else None,
            "target_date": str(issue.target_date) if issue.target_date else None,
            "start_date": str(issue.start_date) if issue.start_date else None,
        }
        # Suppressed: mirrored writes must not re-trigger the push signals.
        with suppress_asana_sync(), transaction.atomic():
            updatable = {
                k: v for k, v in fields.items()
                if k not in ("assignee_ids", "label_ids", "parent_id", "completed_at")
            }
            for key, value in updatable.items():
                setattr(issue, key, value)
            issue.save(update_fields=list(updatable.keys()) + ["updated_at"])
            if "assignee_ids" in fields:
                self._set_assignees(issue, fields["assignee_ids"])
            if "label_ids" in fields:
                self._set_labels(issue, fields["label_ids"])
            self._issue_activity("issue.activity.updated", current, actor_id, issue)
        return issue


def run_sync_pass(sync_id: UUID) -> dict:
    """Entry point used by celery tasks and the manual 'Run now' API."""
    sync = AsanaProjectSync.objects.select_related("connection", "project").filter(id=sync_id).first()
    if sync is None:
        return {"pulled": 0, "pushed": 0, "error": "sync not found"}
    if not sync.is_active or not sync.connection.is_active:
        return {"pulled": 0, "pushed": 0, "error": "sync inactive"}

    from plane.app.asana_sync.crypto import AsanaCryptoError, decrypt_token

    try:
        client = AsanaClient(decrypt_token(sync.connection.pat_encrypted))
    except AsanaCryptoError as exc:
        AsanaSyncLog.objects.create(
            project=sync.project, sync=sync, direction="pull", entity_type="project",
            entity_gid=sync.asana_project_gid, status="error", message=str(exc),
        )
        return {"pulled": 0, "pushed": 0, "error": str(exc)}

    engine = AsanaSyncEngine(sync, client)
    pulled = pushed = 0
    try:
        if sync.direction in ("pull", "bidirectional"):
            pulled = engine.pull_full()
        if sync.direction in ("push", "bidirectional"):
            pushed = engine.push_full()
    except AsanaAuthError:
        AsanaSyncLog.objects.create(
            project=sync.project, sync=sync, direction="pull", entity_type="project",
            entity_gid=sync.asana_project_gid, status="error",
            message="Asana rejected credentials — reconnect required",
        )
        return {"pulled": pulled, "pushed": pushed, "error": "auth"}
    except Exception as exc:
        log_exception(traceback.format_exc())
        return {"pulled": pulled, "pushed": pushed, "error": str(exc)}
    return {"pulled": pulled, "pushed": pushed, "error": None}
