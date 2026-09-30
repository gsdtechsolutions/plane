# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Resolution helpers turning agent-friendly identifiers (project names,
PROJ-14 refs, emails, state names) into model instances, with the same
membership checks the external REST API enforces.
"""

# Python imports
import re
import uuid as uuid_lib

# Django imports
from django.conf import settings
from django.db.models import Q
from django.utils import timezone

# Module imports
from plane.db.models import (
    Issue,
    IssueAssignee,
    IssueComment,
    IssueLabel,
    Label,
    Project,
    ProjectMember,
    State,
    User,
    Workspace,
    WorkspaceMember,
)

ISSUE_REF_PATTERN = re.compile(r"^(?P<identifier>[A-Za-z][A-Za-z0-9_]*)-(?P<sequence>\d+)$")
VALID_PRIORITIES = ["urgent", "high", "medium", "low", "none"]
WRITE_ROLES = [15, 20]
ROLE_NAMES = {20: "Admin", 15: "Member", 5: "Guest"}

# Palette used when auto-creating labels
LABEL_COLORS = ["#3f76ff", "#fcbe1d", "#e8550a", "#bd1b1b", "#6d7cd2", "#0d9373", "#80ac2e", "#99076b"]


class McpToolError(Exception):
    """A semantic tool failure surfaced to the agent as an isError result."""

    def __init__(self, message):
        super().__init__(message)
        self.message = message


def not_found(message):
    return McpToolError(message)


def _active(qs):
    return qs.filter(deleted_at__isnull=True)


def _parse_uuid(value):
    try:
        return uuid_lib.UUID(str(value))
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------- workspace


def get_workspace(request, workspace_slug=None):
    """Resolve the workspace a tool call operates on.

    The API token may pin a workspace; otherwise the token user's first
    active membership wins. An explicit slug is always honored after a
    membership check.
    """
    api_token = getattr(request, "auth", None)
    if isinstance(api_token, str):
        api_token = None

    if workspace_slug:
        try:
            workspace = _active(Workspace.objects).get(slug=workspace_slug)
        except Workspace.DoesNotExist:
            raise not_found(f"Workspace '{workspace_slug}' not found.")
    elif api_token is not None and getattr(api_token, "workspace_id", None):
        workspace = api_token.workspace
    else:
        membership = (
            _active(WorkspaceMember.objects)
            .filter(member=request.user, is_active=True)
            .select_related("workspace")
            .order_by("created_at")
            .first()
        )
        if membership is None:
            raise McpToolError(
                "No workspace is associated with this API token and the token's user is not a member of any workspace."
            )
        workspace = membership.workspace

    if not _active(WorkspaceMember.objects).filter(workspace=workspace, member=request.user, is_active=True).exists():
        raise McpToolError(f"You do not have access to workspace '{workspace.slug}'.")

    return workspace


# ----------------------------------------------------------------- projects


def _member_project_ids(user, workspace):
    return _active(ProjectMember.objects).filter(
        project__workspace=workspace, member=user, is_active=True
    ).values_list("project_id", flat=True)


def list_member_projects(user, workspace):
    project_ids = _member_project_ids(user, workspace)
    return _active(Project.objects).filter(id__in=project_ids).order_by("name")


def get_project(user, workspace, project_ref, require_write=False):
    """Resolve a project by UUID, identifier or name within the caller's
    memberships."""
    if not project_ref:
        raise McpToolError("A project is required (identifier like 'PROJ', name, or UUID).")

    project_ids = list(_member_project_ids(user, workspace))
    queryset = _active(Project.objects).filter(id__in=project_ids, workspace=workspace)

    project = None
    parsed_uuid = _parse_uuid(project_ref)
    if parsed_uuid:
        project = queryset.filter(id=parsed_uuid).first()
    if project is None:
        project = queryset.filter(identifier__iexact=str(project_ref).strip()).first()
    if project is None:
        project = queryset.filter(name__iexact=str(project_ref).strip()).first()
    if project is None:
        raise not_found(
            f"Project '{project_ref}' not found in workspace '{workspace.slug}' "
            "(or you are not a member of it)."
        )

    if require_write:
        membership = (
            _active(ProjectMember.objects)
            .filter(project=project, member=user, is_active=True)
            .first()
        )
        if membership is None or membership.role not in WRITE_ROLES:
            raise McpToolError(
                f"Write access denied for project '{project.identifier}' — member role required."
            )

    return project


# ------------------------------------------------------------------- states


def resolve_state(project, state_ref):
    state = _active(State.objects).filter(project=project, name__iexact=str(state_ref).strip()).first()
    if state is None:
        parsed_uuid = _parse_uuid(state_ref)
        if parsed_uuid:
            state = _active(State.objects).filter(project=project, id=parsed_uuid).first()
    if state is None:
        known = ", ".join(
            _active(State.objects).filter(project=project).values_list("name", flat=True)
        )
        raise not_found(f"State '{state_ref}' not found in project '{project.identifier}'. Known states: {known}.")
    return state


def validate_priority(priority):
    priority = str(priority).lower().strip()
    if priority not in VALID_PRIORITIES:
        raise McpToolError(f"Invalid priority '{priority}'. Valid: {', '.join(VALID_PRIORITIES)}.")
    return priority


# -------------------------------------------------------------------- users


def resolve_assignee_ids(user, workspace, assignees):
    """Accept emails, 'me' or user UUIDs; require workspace membership."""
    if not assignees:
        return []
    resolved = []
    for ref in assignees:
        ref = str(ref).strip()
        if ref.lower() == "me":
            resolved.append(user.id)
            continue
        target = None
        parsed_uuid = _parse_uuid(ref)
        if parsed_uuid:
            target = User.objects.filter(Q(id=parsed_uuid), is_active=True).first()
        if target is None:
            target = User.objects.filter(email__iexact=ref, is_active=True).first()
        if target is None:
            raise not_found(f"Assignee '{ref}' not found.")
        if not _active(WorkspaceMember.objects).filter(workspace=workspace, member=target, is_active=True).exists():
            raise McpToolError(f"'{ref}' is not a member of workspace '{workspace.slug}'.")
        resolved.append(target.id)
    return resolved


# ------------------------------------------------------------------- labels


def resolve_labels(project, names, create_missing=True, creating_user=None):
    """Resolve label names (case-insensitive) to Label instances, creating
    project-scoped labels on demand."""
    resolved = []
    for name in names:
        name = str(name).strip()
        if not name:
            continue
        label = _active(Label.objects).filter(project=project, name__iexact=name).first()
        if label is None and create_missing:
            label = Label.objects.create(
                workspace=project.workspace,
                project=project,
                name=name,
                color=LABEL_COLORS[len(name) % len(LABEL_COLORS)],
                created_by=creating_user,
                updated_by=creating_user,
            )
        if label is None:
            known = ", ".join(
                _active(Label.objects).filter(project=project).values_list("name", flat=True)[:50]
            )
            raise not_found(f"Label '{name}' not found in project '{project.identifier}'. Known labels: {known}.")
        resolved.append(label)
    return resolved


# ------------------------------------------------------------------- issues


def get_issue_by_ref(user, workspace, ref):
    """'PROJ-14' (project identifier + sequence) or a 32-char UUID."""
    ref = str(ref).strip()
    match = ISSUE_REF_PATTERN.match(ref)
    if match:
        project = _active(Project.objects).filter(
            workspace=workspace, identifier__iexact=match.group("identifier")
        ).first()
        if project is None:
            raise not_found(f"No project with identifier '{match.group('identifier')}' in workspace '{workspace.slug}'.")
        issue = (
            Issue.objects.filter(project=project, sequence_id=int(match.group("sequence")))
            .select_related("state", "project", "workspace")
            .first()
        )
        if issue is None:
            raise not_found(f"Work item '{ref}' not found in project '{project.identifier}'.")
        if not _active(ProjectMember.objects).filter(
            project=project, member=user, is_active=True
        ).exists():
            raise McpToolError("You do not have access to this work item's project.")
        return issue

    parsed_uuid = _parse_uuid(ref)
    if parsed_uuid:
        issue = (
            Issue.objects.filter(id=parsed_uuid, workspace=workspace)
            .select_related("state", "project", "workspace")
            .first()
        )
        if issue is None:
            raise not_found(f"Work item '{ref}' not found.")
        if not _active(ProjectMember.objects).filter(
            project=issue.project, member=user, is_active=True
        ).exists():
            raise McpToolError("You do not have access to this work item's project.")
        return issue

    raise McpToolError(
        f"'{ref}' is not a valid work-item reference. Use the human ref (e.g. 'PROJ-14') or a UUID."
    )


def issue_ref(issue):
    return f"{issue.project.identifier}-{issue.sequence_id}"


def issue_url(workspace, issue):
    base = (settings.WEB_URL or "").rstrip("/")
    if not base:
        return None
    return f"{base}/{workspace.slug}/projects/{issue.project_id}/issues/{issue.id}"


# ------------------------------------------------------- payload formatting


def issue_payload(workspace, issue, detailed=False, include_comments=False):
    state = issue.state
    assignees = list(issue.assignees.all()) if detailed else []
    label_names = list(issue.labels.values_list("name", flat=True))

    payload = {
        "id": str(issue.id),
        "ref": issue_ref(issue),
        "name": issue.name,
        "priority": issue.priority,
        "state": state.name if state else None,
        "state_group": state.group if state else None,
        "labels": label_names,
        "url": issue_url(workspace, issue),
        "updated_at": issue.updated_at.isoformat() if issue.updated_at else None,
    }

    if detailed:
        payload.update(
            {
                "assignees": [
                    {"display_name": member.display_name, "email": member.email} for member in assignees
                ],
                "description_html": issue.description_html or "",
                "description_text": issue.description_stripped or "",
                "created_at": issue.created_at.isoformat() if issue.created_at else None,
                "completed_at": issue.completed_at.isoformat() if issue.completed_at else None,
                "parent": issue_ref(issue.parent) if issue.parent_id else None,
                "sub_issues": [
                    {
                        "ref": issue_ref(child),
                        "name": child.name,
                        "state": child.state.name if child.state else None,
                        "priority": child.priority,
                    }
                    for child in Issue.objects.filter(parent=issue).select_related("state")[:50]
                ],
            }
        )
        if include_comments:
            comments = (
                IssueComment.objects.filter(issue=issue, project=issue.project)
                .select_related("actor")
                .order_by("created_at")[:50]
            )
            payload["comments"] = [
                {
                    "id": str(comment.id),
                    "author": comment.actor.display_name if comment.actor else None,
                    "created_at": comment.created_at.isoformat() if comment.created_at else None,
                    "text": (comment.comment_stripped or "")[:2000],
                }
                for comment in comments
            ]
    else:
        payload["description_preview"] = (issue.description_stripped or "")[:300]

    return payload


# ------------------------------------------------------------ write helpers


def set_assignees(issue, user_ids, actor):
    """Replace assignees through direct through-rows.

    ``issue.assignees.set()`` fails on this fork — IssueAssignee carries NOT
    NULL project/workspace columns (see github_delivery `_assign_issue`).
    """
    IssueAssignee.objects.filter(issue=issue).delete()
    IssueAssignee.objects.bulk_create(
        IssueAssignee(
            issue=issue,
            assignee_id=user_id,
            project_id=issue.project_id,
            workspace_id=issue.workspace_id,
            created_by=actor,
            updated_by=actor,
        )
        for user_id in user_ids
    )


def set_labels(issue, labels, actor):
    IssueLabel.objects.filter(issue=issue).delete()
    IssueLabel.objects.bulk_create(
        IssueLabel(
            issue=issue,
            label=label,
            project_id=issue.project_id,
            workspace_id=issue.workspace_id,
            created_by=actor,
            updated_by=actor,
        )
        for label in labels
    )


def now_iso():
    return timezone.now().isoformat()
