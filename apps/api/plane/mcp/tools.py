# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""MCP tool registry: schema definitions plus handlers.

Handlers receive the DRF request (authenticated Plane user) and the raw
``arguments`` dict, resolve the workspace/project through
``plane.mcp.resolvers`` and return JSON-serializable payloads. Semantic
failures raise ``McpToolError`` and surface as isError tool results.
"""

# Python imports
import json

# Django imports
from django.conf import settings
from django.db import transaction
from django.db.models import Q

# Module imports
from plane.db.models import Issue, IssueComment, Label, ProjectMember, State, WorkspaceMember
from plane.mcp import resolvers
from plane.mcp.resolvers import McpToolError

# --------------------------------------------------------------------- utils


def _slug_arg(args):
    return args.pop("workspace_slug", None)


def _require_project_arg(args):
    ref = args.pop("project", None)
    if not ref:
        raise McpToolError("Parameter 'project' is required (identifier like 'PROJ', name, or UUID).")
    return ref


def _web_base():
    return (settings.WEB_URL or "").rstrip("/")


def _project_url(workspace, project):
    base = _web_base()
    return f"{base}/{workspace.slug}/projects/{project.id}" if base else None


def _description_html(args, *, allow_update=False):
    """Pop description_text/description_html from args and normalize.

    Returns the effective description_html, or None on update when no
    description field was provided.
    """
    description_html = args.pop("description_html", None)
    description_text = args.pop("description_text", None)
    if description_html is not None and description_text is not None:
        raise McpToolError("Provide either description_text or description_html, not both.")
    if description_text is not None:
        description_html = f"<p>{description_text}</p>"
    if not allow_update and description_html is None:
        description_html = "<p></p>"
    return description_html


def _reject_unknown(args):
    if args:
        raise McpToolError(f"Unknown parameter(s): {', '.join(sorted(args.keys()))}.")


# ------------------------------------------------------------------- handlers


def tool_list_projects(request, args):
    workspace = resolvers.get_workspace(request, _slug_arg(args))
    projects = resolvers.list_member_projects(request.user, workspace)
    return {
        "count": len(projects),
        "projects": [
            {
                "id": str(project.id),
                "identifier": project.identifier,
                "name": project.name,
                "description": (project.description_text or project.description or "")[:500],
                "url": _project_url(workspace, project),
            }
            for project in projects
        ],
    }


def tool_list_states(request, args):
    workspace = resolvers.get_workspace(request, _slug_arg(args))
    project = resolvers.get_project(request.user, workspace, _require_project_arg(args))
    states = resolvers._active(State.objects).filter(project=project).order_by("sequence")
    return {
        "project": project.identifier,
        "states": [
            {
                "id": str(state.id),
                "name": state.name,
                "group": state.group,
                "color": state.color,
                "is_default": bool(state.default),
            }
            for state in states
        ],
    }


def tool_list_members(request, args):
    workspace = resolvers.get_workspace(request, _slug_arg(args))
    project_ref = args.pop("project", None)
    _reject_unknown(args)

    if project_ref:
        project = resolvers.get_project(request.user, workspace, project_ref)
        rows = (
            resolvers._active(ProjectMember.objects)
            .filter(project=project, is_active=True)
            .select_related("member")
        )
    else:
        rows = (
            resolvers._active(WorkspaceMember.objects)
            .filter(workspace=workspace, is_active=True)
            .select_related("member")
        )

    members = [
        {
            "id": str(row.member_id),
            "email": row.member.email,
            "display_name": row.member.display_name,
            "role": resolvers.ROLE_NAMES.get(row.role, str(row.role)),
        }
        for row in rows
    ]
    return {"count": len(members), "members": members}


def tool_list_labels(request, args):
    workspace = resolvers.get_workspace(request, _slug_arg(args))
    project = resolvers.get_project(request.user, workspace, _require_project_arg(args))
    _reject_unknown(args)
    labels = resolvers._active(Label.objects).filter(project=project).order_by("name")
    return {
        "project": project.identifier,
        "labels": [{"id": str(label.id), "name": label.name, "color": label.color} for label in labels],
    }


def tool_list_issues(request, args):
    workspace = resolvers.get_workspace(request, _slug_arg(args))
    user = request.user
    project_ref = args.pop("project", None)
    project = resolvers.get_project(user, workspace, project_ref) if project_ref else None

    project_ids = [project.id] if project else list(resolvers._member_project_ids(user, workspace))
    if not project_ids:
        return {"count": 0, "total": 0, "offset": 0, "limit": 0, "results": []}

    queryset = Issue.objects.filter(project_id__in=project_ids, workspace=workspace).select_related(
        "state", "project"
    )

    state_ref = args.pop("state", None)
    if state_ref:
        queryset = queryset.filter(
            state__in=resolvers._active(State.objects).filter(name__iexact=str(state_ref).strip())
        )

    state_group = args.pop("state_group", None)
    if state_group:
        queryset = queryset.filter(state__group=str(state_group).lower().strip())

    priority = args.pop("priority", None)
    if priority:
        queryset = queryset.filter(priority=resolvers.validate_priority(priority))

    assignee = args.pop("assignee", None)
    if assignee:
        assignee_ids = resolvers.resolve_assignee_ids(user, workspace, [assignee])
        queryset = queryset.filter(assignees__in=assignee_ids)

    label = args.pop("label", None)
    if label:
        if project is None:
            raise McpToolError("The 'label' filter requires the 'project' parameter.")
        label_rows = resolvers.resolve_labels(project, [label], create_missing=False)
        queryset = queryset.filter(labels__in=label_rows)

    search = args.pop("search", None)
    if search:
        queryset = queryset.filter(Q(name__icontains=search) | Q(description_stripped__icontains=search))

    include_closed = bool(args.pop("include_closed", False))
    if not include_closed:
        queryset = queryset.exclude(state__group__in=["completed", "cancelled"])

    try:
        limit = max(1, min(int(args.pop("limit", 25)), 100))
        offset = max(0, int(args.pop("offset", 0)))
    except (TypeError, ValueError):
        raise McpToolError("'limit' and 'offset' must be integers.")

    _reject_unknown(args)

    total = queryset.count()
    issues = queryset.order_by("-updated_at")[offset : offset + limit]

    return {
        "count": len(issues),
        "total": total,
        "offset": offset,
        "limit": limit,
        "results": [resolvers.issue_payload(workspace, issue) for issue in issues],
    }


def tool_get_issue(request, args):
    workspace = resolvers.get_workspace(request, _slug_arg(args))
    ref = args.pop("issue", None)
    if not ref:
        raise McpToolError("Parameter 'issue' is required (e.g. 'PROJ-14').")
    include_comments = bool(args.pop("include_comments", True))
    _reject_unknown(args)
    issue = resolvers.get_issue_by_ref(request.user, workspace, ref)
    return resolvers.issue_payload(workspace, issue, detailed=True, include_comments=include_comments)


def tool_create_issue(request, args):
    workspace = resolvers.get_workspace(request, _slug_arg(args))
    user = request.user
    project = resolvers.get_project(
        user, workspace, _require_project_arg(args), require_write=True
    )

    name = args.pop("name", None)
    if not name or not str(name).strip():
        raise McpToolError("Parameter 'name' is required.")
    name = str(name).strip()

    priority_arg = args.pop("priority", None)
    priority = resolvers.validate_priority(priority_arg) if priority_arg is not None else "none"
    description_html = _description_html(args)
    state_ref = args.pop("state", None)
    parent_ref = args.pop("parent", None)
    assignees = args.pop("assignees", None)
    labels = args.pop("labels", None)
    _reject_unknown(args)

    with transaction.atomic():
        state = resolvers.resolve_state(project, state_ref) if state_ref else None
        parent = resolvers.get_issue_by_ref(user, workspace, parent_ref) if parent_ref else None
        assignee_ids = resolvers.resolve_assignee_ids(user, workspace, assignees or [])
        label_rows = resolvers.resolve_labels(project, labels or [], creating_user=user)

        issue = Issue(
            project=project,
            workspace=workspace,
            name=name,
            description_html=description_html,
            priority=priority,
            state=state,
            parent=parent,
            created_by=user,
            updated_by=user,
        )
        issue.save()
        resolvers.set_assignees(issue, assignee_ids, user)
        resolvers.set_labels(issue, label_rows, user)

    return resolvers.issue_payload(workspace, issue, detailed=True, include_comments=False)


def tool_update_issue(request, args):
    workspace = resolvers.get_workspace(request, _slug_arg(args))
    user = request.user
    ref = args.pop("issue", None)
    if not ref:
        raise McpToolError("Parameter 'issue' is required (e.g. 'PROJ-14').")

    issue = resolvers.get_issue_by_ref(user, workspace, ref)
    resolvers.get_project(user, workspace, str(issue.project_id), require_write=True)

    name = args.pop("name", None)
    if name is not None and not str(name).strip():
        raise McpToolError("'name' cannot be empty.")
    description_has_update = "description_html" in args or "description_text" in args
    description_html = _description_html(args, allow_update=True) if description_has_update else None
    state_ref = args.pop("state", None)
    priority = args.pop("priority", None)
    assignees = args.pop("assignees", None)
    labels = args.pop("labels", None)
    _reject_unknown(args)

    with transaction.atomic():
        if name is not None:
            issue.name = str(name).strip()
        if description_html is not None:
            issue.description_html = description_html
        if priority is not None:
            issue.priority = resolvers.validate_priority(priority)
        if state_ref is not None:
            issue.state = resolvers.resolve_state(issue.project, state_ref)
        if assignees is not None:
            resolvers.set_assignees(issue, resolvers.resolve_assignee_ids(user, workspace, assignees), user)
        if labels is not None:
            resolvers.set_labels(
                issue, resolvers.resolve_labels(issue.project, labels, creating_user=user), user
            )
        issue.updated_by = user
        issue.save()

    return resolvers.issue_payload(workspace, issue, detailed=True, include_comments=False)


def tool_add_comment(request, args):
    workspace = resolvers.get_workspace(request, _slug_arg(args))
    user = request.user
    ref = args.pop("issue", None)
    if not ref:
        raise McpToolError("Parameter 'issue' is required (e.g. 'PROJ-14').")
    comment_text = args.pop("comment", None)
    if not comment_text or not str(comment_text).strip():
        raise McpToolError("Parameter 'comment' is required.")
    _reject_unknown(args)

    issue = resolvers.get_issue_by_ref(user, workspace, ref)
    resolvers.get_project(user, workspace, str(issue.project_id), require_write=True)

    comment = IssueComment.objects.create(
        issue=issue,
        project=issue.project,
        workspace=issue.workspace,
        actor=user,
        comment_html=f"<p>{str(comment_text).strip()}</p>",
        created_by=user,
        updated_by=user,
    )
    return {
        "id": str(comment.id),
        "issue": resolvers.issue_ref(issue),
        "author": user.display_name,
        "created_at": resolvers.now_iso(),
        "text": (comment.comment_stripped or "")[:2000],
    }


def tool_delete_issue(request, args):
    workspace = resolvers.get_workspace(request, _slug_arg(args))
    user = request.user
    ref = args.pop("issue", None)
    if not ref:
        raise McpToolError("Parameter 'issue' is required (e.g. 'PROJ-14').")
    _reject_unknown(args)

    issue = resolvers.get_issue_by_ref(user, workspace, ref)
    resolvers.get_project(user, workspace, str(issue.project_id), require_write=True)
    ref_value = resolvers.issue_ref(issue)
    issue.delete()
    return {"deleted": True, "ref": ref_value}


# --------------------------------------------------------------- definitions


def _tool(name, description, properties, required=None, write=False):
    schema = {"type": "object", "properties": dict(properties)}
    if required:
        schema["required"] = required
    return {"name": name, "description": description, "inputSchema": schema, "write": write}


WORKSPACE_ARG = {
    "workspace_slug": {
        "type": "string",
        "description": "Workspace slug. Defaults to the API token's workspace.",
    }
}

PROJECT_ARG = {
    "project": {
        "type": "string",
        "description": "Project identifier (e.g. 'PROJ'), name or UUID.",
    }
}

TOOLS = [
    _tool(
        "plane_list_projects",
        "List the Plane projects in your workspace that you can access.",
        WORKSPACE_ARG,
    ),
    _tool(
        "plane_list_states",
        "List the workflow states (columns) of a Plane project, with their groups.",
        {**PROJECT_ARG, **WORKSPACE_ARG},
        required=["project"],
    ),
    _tool(
        "plane_list_members",
        "List workspace members (or a project's members) with emails, usable as assignees.",
        {**WORKSPACE_ARG, "project": {"type": "string", "description": "Optional project to scope the list."}},
    ),
    _tool(
        "plane_list_labels",
        "List the labels defined on a Plane project.",
        {**PROJECT_ARG, **WORKSPACE_ARG},
        required=["project"],
    ),
    _tool(
        "plane_list_issues",
        "Search/list work items. Filters: project (omit to search all your projects), state, "
        "state_group (backlog/unstarted/started/completed/cancelled), priority, assignee (email or 'me'), "
        "label (requires project), search text, include_closed.",
        {
            **WORKSPACE_ARG,
            "project": {"type": "string", "description": "Optional project identifier/name/UUID."},
            "state": {"type": "string"},
            "state_group": {
                "type": "string",
                "enum": ["backlog", "unstarted", "started", "completed", "cancelled"],
            },
            "priority": {"type": "string", "enum": ["urgent", "high", "medium", "low", "none"]},
            "assignee": {"type": "string", "description": "User email or 'me'."},
            "label": {"type": "string", "description": "Label name (requires 'project')."},
            "search": {"type": "string", "description": "Substring match on name and description."},
            "include_closed": {"type": "boolean", "description": "Include completed/cancelled items. Default false."},
            "limit": {"type": "integer", "description": "1-100, default 25."},
            "offset": {"type": "integer", "description": "Pagination offset, default 0."},
        },
    ),
    _tool(
        "plane_get_issue",
        "Get one work item in full: description, state, priority, assignees, labels, comments, "
        "sub-issues and URL.",
        {
            **WORKSPACE_ARG,
            "issue": {"type": "string", "description": "Work item ref like 'PROJ-14' or a UUID."},
            "include_comments": {"type": "boolean", "description": "Default true."},
        },
        required=["issue"],
    ),
    _tool(
        "plane_create_issue",
        "Create a work item in a Plane project. Returns the new item with its human ref.",
        {
            **WORKSPACE_ARG,
            "project": {"type": "string", "description": "Project identifier (e.g. 'PROJ'), name or UUID."},
            "name": {"type": "string", "description": "Work item title."},
            "description_text": {"type": "string", "description": "Plain-text description (preferred)."},
            "description_html": {"type": "string", "description": "HTML description (alternative)."},
            "priority": {"type": "string", "enum": ["urgent", "high", "medium", "low", "none"]},
            "state": {
                "type": "string",
                "description": "State name, case-insensitive. Defaults to the project's default state.",
            },
            "assignees": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Emails (workspace members) or 'me'.",
            },
            "labels": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Label names; missing labels are created.",
            },
            "parent": {"type": "string", "description": "Parent work item ref, e.g. 'PROJ-7'."},
        },
        required=["project", "name"],
        write=True,
    ),
    _tool(
        "plane_update_issue",
        "Update a work item: name, description, priority, state, assignees and/or labels. "
        "assignees/labels replace the current set when provided; omit them to leave untouched.",
        {
            **WORKSPACE_ARG,
            "issue": {"type": "string", "description": "Work item ref like 'PROJ-14'."},
            "name": {"type": "string"},
            "description_text": {"type": "string"},
            "description_html": {"type": "string"},
            "priority": {"type": "string", "enum": ["urgent", "high", "medium", "low", "none"]},
            "state": {"type": "string"},
            "assignees": {"type": "array", "items": {"type": "string"}, "description": "Full replacement set."},
            "labels": {"type": "array", "items": {"type": "string"}, "description": "Full replacement set."},
        },
        required=["issue"],
        write=True,
    ),
    _tool(
        "plane_add_comment",
        "Add a comment to a work item.",
        {
            **WORKSPACE_ARG,
            "issue": {"type": "string", "description": "Work item ref like 'PROJ-14'."},
            "comment": {"type": "string", "description": "Comment text."},
        },
        required=["issue", "comment"],
        write=True,
    ),
    _tool(
        "plane_delete_issue",
        "Permanently delete a work item. Destructive — use only when the user clearly asked.",
        {
            **WORKSPACE_ARG,
            "issue": {"type": "string", "description": "Work item ref like 'PROJ-14'."},
        },
        required=["issue"],
        write=True,
    ),
]

TOOL_HANDLERS = {
    "plane_list_projects": tool_list_projects,
    "plane_list_states": tool_list_states,
    "plane_list_members": tool_list_members,
    "plane_list_labels": tool_list_labels,
    "plane_list_issues": tool_list_issues,
    "plane_get_issue": tool_get_issue,
    "plane_create_issue": tool_create_issue,
    "plane_update_issue": tool_update_issue,
    "plane_add_comment": tool_add_comment,
    "plane_delete_issue": tool_delete_issue,
}

TOOL_DEFINITIONS = [
    {
        "name": tool["name"],
        "title": tool["name"],
        "description": tool["description"],
        "inputSchema": tool["inputSchema"],
        "annotations": {"readOnlyHint": not tool["write"]},
    }
    for tool in TOOLS
]


def call_tool(request, name, arguments):
    handler = TOOL_HANDLERS.get(name)
    if handler is None:
        raise KeyError(name)

    arguments = dict(arguments or {})
    spec = next((tool for tool in TOOLS if tool["name"] == name), None)
    for required_field in spec["inputSchema"].get("required", []):
        if required_field not in arguments or arguments[required_field] in (None, ""):
            raise McpToolError(f"Missing required parameter '{required_field}'.")

    payload = handler(request, arguments)
    if isinstance(payload, str):
        return payload
    return json.dumps(payload, indent=2, default=str)
