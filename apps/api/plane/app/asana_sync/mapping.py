# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Pure field mapping between Asana tasks and Plane issues.

No ORM access here — everything is deterministic and unit-testable. The engine
resolves ids (state/label/assignee/parent) and calls these helpers to build
payloads in either direction.

Documented semantics (mirrors the official Asana importer, adapted for
continuous sync):
- description: Asana html_notes <-> Plane description_html (tag normalization below)
- state: Asana section -> Plane state via sync.state_map (auto-provisioned by the
  engine); completed=true forces a completed-group state when the mapped one isn't
- priority: Asana has no priority field — pulled tasks keep the project default
- assignee: via sync.assignee_map (Asana gid -> Plane member id), unmapped = none
- labels: Asana tags -> Plane labels via sync.label_map (engine auto-provisions)
- due date: Asana due_on -> Plane target_date, start_on -> start_date
- completion: Plane state group completed/cancelled -> Asana completed=true
"""

# Python imports
import hashlib
import re
from datetime import datetime
from typing import Any, Optional

# Module imports
from plane.utils.html_processor import strip_tags

_ASANA_BODY_RE = re.compile(r"^\s*<body>(.*)</body>\s*$", re.DOTALL | re.IGNORECASE)

# Asana emits bare <li> items; Plane prose expects <ul>/<ol> wrappers.
_LIST_ITEMS_RE = re.compile(r"(<li\b.*?</li>)(?!\s*<li)", re.DOTALL | re.IGNORECASE)

_ALLOWED_ASANA_TAGS = {"p", "br", "a", "strong", "b", "em", "i", "code", "li", "ul", "ol", "blockquote"}
_PLANE_BLOCK_TAGS = {"p", "ul", "ol", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6", "table", "pre", "hr", "div"}
_PLANE_INLINE_TAGS = {"a", "strong", "b", "em", "i", "code", "span", "u", "s", "sub", "sup"}


def _sanitize_allowed(html: str, allowed: set[str]) -> str:
    """Drop tags outside `allowed`, keep their text; drop script/style entirely."""

    def _drop_hidden(match: re.Match) -> str:
        return ""

    html = re.sub(r"<(script|style)\b.*?</\1\s*>", _drop_hidden, html, flags=re.DOTALL | re.IGNORECASE)

    def _repl(match: re.Match) -> str:
        closing, tag_name = match.group(1), (match.group(2) or "").lower()
        if tag_name in allowed:
            return match.group(0)
        return ""

    return re.sub(r"<\s*(/?)\s*([a-zA-Z0-9]+)[^>]*>", _repl, html)


def asana_html_to_plane_html(html_notes: str) -> str:
    """Convert Asana html_notes (e.g. `<body><p>Hi <a ...>x</a></p><li>a</li></body>`)
    into Plane description_html. Unrecognized tags are stripped, never executed."""
    if not html_notes:
        return "<p></p>"
    body_match = _ASANA_BODY_RE.match(html_notes)
    html = body_match.group(1) if body_match else html_notes
    html = _sanitize_allowed(html, _ALLOWED_ASANA_TAGS)

    def _wrap_lists(match: re.Match) -> str:
        return "<ul>" + match.group(1) + "</ul>"

    html = _LIST_ITEMS_RE.sub(_wrap_lists, html)
    html = re.sub(r"<br\s*/?>", "<br/>", html, flags=re.IGNORECASE)
    html = html.strip()
    # Bare inline text becomes a paragraph; already-block content passes through.
    if html and not html.lower().startswith(("<p", "<ul", "<ol", "<blockquote", "<h", "<li", "<pre", "<div")):
        html = f"<p>{html}</p>"
    return html or "<p></p>"


def plane_html_to_asana_notes_html(description_html: str) -> str:
    """Convert Plane description_html into Asana html_notes (`<body>...</body>` shape)."""
    if not description_html:
        description_html = "<p></p>"
    html = _sanitize_allowed(description_html, _ALLOWED_ASANA_TAGS)
    # Asana renders nested block tags poorly; unwrap lists' wrapper to bare <li> items.
    html = re.sub(r"</?(ul|ol)\b[^>]*>", "", html, flags=re.IGNORECASE)
    html = html.strip()
    return f"<body>{html}</body>"


def text_to_asana_story_html(text: str) -> str:
    """Plane comment (plain text) -> Asana story html_text (escaped)."""
    escaped = (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br/>")
    )
    return f"<body>{escaped}</body>"


def asana_story_to_comment_html(html_text: str) -> str:
    """Asana story html_text -> Plane comment_html."""
    if not html_text:
        return "<p></p>"
    return asana_html_to_plane_html(html_text)


def asana_datetime(value: Optional[str]) -> Optional[datetime]:
    """Parse Asana's ISO-8601 timestamps (trailing Z)."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def asana_date(value: Optional[str]) -> Optional[str]:
    """Asana due_on/start_on (YYYY-MM-DD) passed through, or None."""
    if not value:
        return None
    return str(value)[:10]


def section_gid_of(task: dict) -> Optional[str]:
    """First section membership of a task, or None (unsectioned)."""
    for membership in task.get("memberships") or []:
        if membership.get("section"):
            return membership["section"]
    return None


def content_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def build_issue_fields_from_task(
    task: dict,
    *,
    state_id: Optional[str],
    assignee_map: dict[str, str],
    label_map: dict[str, str],
    known_tag_ids: set[str],
    parent_issue_id: Optional[str],
) -> dict[str, Any]:
    """Plane-side field payload for creating/updating an issue from an Asana task."""
    fields: dict[str, Any] = {
        "name": task.get("name") or "Untitled task",
        "description_html": asana_html_to_plane_html(task.get("html_notes") or ""),
    }
    if state_id:
        fields["state_id"] = str(state_id)
    assignee_gid = (task.get("assignee") or {}).get("gid") if isinstance(task.get("assignee"), dict) else task.get("assignee")
    if assignee_gid and assignee_gid in assignee_map:
        fields["assignee_ids"] = [assignee_map[assignee_gid]]
    else:
        fields["assignee_ids"] = []
    labels: list[str] = []
    for tag in task.get("tags") or []:
        tag_gid = tag.get("gid") if isinstance(tag, dict) else tag
        if tag_gid and tag_gid in label_map:
            labels.append(label_map[tag_gid])
        elif tag_gid and tag_gid in known_tag_ids:
            # Engine provisions ids lazily; between provisioning and map write the
            # engine passes the fresh map — this branch covers that ordering.
            labels.append(tag_gid)
    if labels:
        fields["label_ids"] = labels
    due = asana_date(task.get("due_on"))
    if due:
        fields["target_date"] = due
    start = asana_date(task.get("start_on"))
    if start:
        fields["start_date"] = start
    if task.get("completed_at"):
        completed_at = asana_datetime(task["completed_at"])
        if completed_at:
            fields["completed_at"] = completed_at
    if parent_issue_id:
        fields["parent_id"] = str(parent_issue_id)
    return fields


def build_task_payload_from_issue(
    issue: Any,
    *,
    description_html: str,
    completed: bool,
    assignee_gid: Optional[str],
    tag_gids: list[str],
    parent_task_gid: Optional[str] = None,
) -> dict[str, Any]:
    """Asana-side task payload for creating/updating a task from a Plane issue."""
    payload: dict[str, Any] = {
        "name": issue.name or "Untitled issue",
        "html_notes": plane_html_to_asana_notes_html(description_html),
        "completed": bool(completed),
    }
    if getattr(issue, "target_date", None):
        payload["due_on"] = str(issue.target_date)
    if getattr(issue, "start_date", None):
        payload["start_on"] = str(issue.start_date)
    if assignee_gid:
        payload["assignee"] = assignee_gid
    if tag_gids:
        payload["tags"] = tag_gids
    if parent_task_gid:
        payload["parent"] = parent_task_gid
    return payload


def comment_text_for_asana(comment_html: str) -> str:
    """Plane comment_html -> plain text for Asana story text (stories are text-only)."""
    return strip_tags(comment_html or "").strip()
