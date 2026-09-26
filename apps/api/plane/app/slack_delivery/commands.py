# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only

import html
import re

from crum import impersonate

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
)
from plane.db.models.slack_delivery import SlackChannelMapping, SlackConnection
from .client import (
    RESPONSE_URL_PREFIX,
    SlackClient,
    SlackUnavailable,
    bot_token,
    configuration,
    slack_blocks_issue,
    slack_blocks_list,
)
from . import services

MAX_TEXT = 4000
TITLE_MIN = 3
TITLE_MAX = 512
LIST_LIMIT = 15
MAX_LABELS = 10
COMMANDS = ("help", "create", "view", "assign", "label", "state", "comment", "close", "list")

# Any host is accepted: ids are extracted directly and re-scoped to the
# connection's workspace, so a foreign origin cannot widen access.
ISSUE_URL_RE = re.compile(
    r"^https?://[^/\s@]+/[^/\s@]+/projects/"
    r"(?P<project>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
    r"/issues/(?P<issue>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/?$",
    re.IGNORECASE,
)
KEY_RE = re.compile(r"^([A-Za-z][A-Za-z0-9]{0,19})-([0-9]{1,10})$")
NUMBER_RE = re.compile(r"^[0-9]{1,10}$")
MENTION_RE = re.compile(r"^<@([A-Z][A-Z0-9]{5,30})(?:\|[^>]*)?>$")
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+$")

HELP_TEXT = "\n".join(
    [
        "*Plane commands*",
        "• `/plane help` — show this help",
        "• `/plane create <title>` — create an issue in this channel's project",
        "• `/plane view <ref>` — show a work item",
        "• `/plane KEY-12` — quick lookup, answered only to you",
        "• `/plane assign <ref> <who>` — assign a member (@mention, email, or full name)",
        "• `/plane label <ref> <labels>` — add comma-separated labels (missing ones are created)",
        "• `/plane state <ref> <name>` — move a work item to a state",
        "• `/plane comment <ref> <text>` — comment on a work item",
        "• `/plane close <ref>` — move a work item to the completed state",
        "• `/plane list [state]` — show up to 15 issues (open by default)",
        "A <ref> is `KEY-12`, a plain number, or a Plane issue URL.",
    ]
)


class CommandError(Exception):
    """User-visible failure posted back to Slack as an ephemeral message."""


def slack_escape(value):
    """Strip angle brackets so user text cannot inject Slack link syntax."""
    return value.replace("<", "").replace(">", "")


def member_display(user):
    name = f"{user.first_name or ''} {user.last_name or ''}".strip()
    return name or "unnamed member"


def parse(text):
    """Split a command body into its action word and the remaining text."""
    body = (text or "").strip()
    parts = body.split(None, 1)
    action = parts[0].lower() if parts else ""
    if action not in COMMANDS:
        # A bare `KEY-12` or number (no subcommand word) is a quick lookup;
        # anything else unknown still answers help.
        if KEY_RE.fullmatch(body) or NUMBER_RE.fullmatch(body):
            return {"action": "lookup", "rest": body}
        action = "help"
    return {"action": action, "rest": parts[1] if len(parts) > 1 else ""}


def issue_url_ids(value):
    match = ISSUE_URL_RE.fullmatch((value or "").strip())
    return (match.group("project"), match.group("issue")) if match else None


def issue_key(issue):
    return f"{issue.project.identifier}-{issue.sequence_id}"


def board_link(issue):
    base = configuration()["BASE_URL"].rstrip("/")
    return f"{base}/{issue.project.workspace.slug}/projects/{issue.project_id}/issues/{issue.id}"


def summary_line(issue):
    parts = [f"State: {slack_escape(issue.state.name) if issue.state else 'none'}"]
    assignees = [member_display(user) for user in issue.assignees.all()]
    if assignees:
        parts.append(f"Assignees: {', '.join(assignees)}")
    labels = [label.name for label in issue.labels.all()]
    if labels:
        parts.append(f"Labels: {', '.join(slack_escape(name) for name in labels)}")
    return " · ".join(parts)


def issue_message(verb, issue):
    return "\n".join(
        (
            f"{verb} {issue_key(issue)} · {slack_escape(issue.name)}",
            summary_line(issue),
            board_link(issue),
        )
    )


def active_connection(team_id):
    connection = (
        SlackConnection.objects.select_related("workspace").filter(team_id=team_id, is_active=True).first()
    )
    if connection is None:
        base = configuration()["BASE_URL"].rstrip("/")
        hint = f' Open {{base}}/<workspace-slug>/settings/integrations and click "Connect Slack".' if base else ""
        raise CommandError(
            "This Slack workspace is not connected to a Plane workspace yet."
            + hint
            + " A workspace admin only needs to do this once."
        )
    return connection


def channel_mapping(connection, channel_id):
    return (
        SlackChannelMapping.objects.filter(
            connection=connection,
            channel_id=channel_id,
            is_active=True,
            project__workspace_id=connection.workspace_id,
            project__deleted_at__isnull=True,
        )
        .select_related("project", "project__workspace")
        .first()
    )


def require_mapping(mapping):
    if mapping is None:
        raise CommandError(
            "This channel is not mapped to a Plane project. Map it in Plane's integration settings first."
        )
    return mapping


def workspace_member(connection, email):
    return (
        User.objects.filter(
            email__iexact=email,
            member_workspace__workspace_id=connection.workspace_id,
            member_workspace__is_active=True,
        )
        .order_by("created_at")
        .first()
    )


def profile_email(profile):
    data = profile.get("profile") if isinstance(profile, dict) else None
    email = data.get("email") if isinstance(data, dict) else None
    return email if isinstance(email, str) and EMAIL_RE.fullmatch(email) else None


def actor_user(connection, token, user_id):
    try:
        profile = SlackClient().user_info(token, user_id)
    except SlackUnavailable:
        raise CommandError("Plane could not read your Slack profile. Reconnect Slack and try again.")
    email = profile_email(profile)
    if not email:
        raise CommandError("Your Slack profile has no email. Add one and try again.")
    member = workspace_member(connection, email)
    if member is None:
        raise CommandError(f"Your Slack account email {email} is not a member of this Plane workspace.")
    return member


def require_project_member(actor, project):
    if not ProjectMember.objects.filter(project=project, member=actor, is_active=True, role__gte=15).exists():
        raise CommandError(f"You are not an active member of the project {project.identifier}.")


def issue_query():
    return (
        Issue.objects.select_related("project", "project__workspace", "state")
        .prefetch_related("assignees", "labels")
    )


def issue_for_ids(connection, project_id, issue_id):
    issue = issue_query().filter(
        id=issue_id, project_id=project_id, project__workspace_id=connection.workspace_id
    ).first()
    if issue is None:
        raise CommandError("That Plane work item does not exist in this workspace.")
    return issue


def resolve_ref(connection, mapping, ref):
    """Resolve KEY-number, a bare number, or a Plane issue URL to a work item."""
    ref = (ref or "").strip()
    if not ref:
        raise CommandError("Which work item? Use a key like DEV-12, a number, or a Plane issue URL.")
    ids = issue_url_ids(ref)
    if ids:
        return issue_for_ids(connection, ids[0], ids[1])
    key = KEY_RE.fullmatch(ref)
    if key:
        project = Project.objects.filter(
            identifier__iexact=key[1], workspace_id=connection.workspace_id, deleted_at__isnull=True
        ).first()
        if project is None:
            raise CommandError(f"No project with identifier {key[1].upper()} exists in this workspace.")
        issue = issue_query().filter(project=project, sequence_id=int(key[2])).first()
        if issue is None:
            raise CommandError(f"No work item {key[1].upper()}-{key[2]} exists.")
        return issue
    if NUMBER_RE.fullmatch(ref):
        if mapping is None:
            raise CommandError("A bare number needs a channel mapped to a project. Use a KEY-12 reference instead.")
        issue = issue_query().filter(project=mapping.project, sequence_id=int(ref)).first()
        if issue is None:
            raise CommandError(f"No work item {mapping.project.identifier}-{ref} exists.")
        return issue
    raise CommandError(f"{slack_escape(ref)} is not a work item reference. Use KEY-12, a number, or a Plane issue URL.")


def resolve_member(connection, token, who):
    """Resolve a Slack mention, an email, or a full name to a workspace member."""
    who = (who or "").strip()
    if not who:
        raise CommandError("Assign to whom? Use an @mention, an email, or a full name.")
    mention = MENTION_RE.fullmatch(who)
    if mention:
        try:
            profile = SlackClient().user_info(token, mention[1])
        except SlackUnavailable:
            raise CommandError("Plane could not read that Slack member's profile. Use an email or full name instead.")
        email = profile_email(profile)
        member = workspace_member(connection, email) if email else None
        if member is None:
            # Never echo the looked-up address: only the actor's own input.
            raise CommandError("The Slack member you mentioned is not a member of this Plane workspace.")
        return member
    if EMAIL_RE.fullmatch(who):
        member = workspace_member(connection, who)
        if member is None:
            raise CommandError(f"No active member of this Plane workspace matches {slack_escape(who)}.")
        return member
    wanted = who.casefold()
    matches = {}
    for member in User.objects.filter(
        member_workspace__workspace_id=connection.workspace_id,
        member_workspace__is_active=True,
    )[:500]:
        names = {
            f"{member.first_name or ''} {member.last_name or ''}".strip().casefold(),
            (member.first_name or "").casefold(),
            (member.last_name or "").casefold(),
        }
        names.discard("")
        if wanted in names:
            matches[member.id] = member
    if not matches:
        raise CommandError(f"No member of this Plane workspace matches {slack_escape(who)}. Use an email or @mention.")
    if len(matches) > 1:
        raise CommandError(f"Several members match {slack_escape(who)}. Use an email or @mention.")
    return next(iter(matches.values()))


def split_ref(rest):
    parts = rest.strip().split(None, 1)
    if not parts:
        raise CommandError("Which work item? Use a key like DEV-12, a number, or a Plane issue URL.")
    return parts[0], (parts[1] if len(parts) > 1 else "").strip()


def command_create(connection, mapping, actor, rest):
    title = rest.strip()
    if not TITLE_MIN <= len(title) <= TITLE_MAX:
        raise CommandError(f"Give the issue a title of {TITLE_MIN} to {TITLE_MAX} characters.")
    issue = Issue.objects.create(
        project=mapping.project,
        workspace=mapping.project.workspace,
        name=title,
    )
    return {"response_type": "in_channel", "text": issue_message("Created", issue)}


def command_view(connection, mapping, actor, rest):
    ref, extra = split_ref(rest)
    if extra:
        raise CommandError("View takes a single work item reference.")
    issue = resolve_ref(connection, mapping, ref)
    require_project_member(actor, issue.project)
    blocks, _ = slack_blocks_issue(issue)
    return {
        "response_type": "in_channel",
        "text": issue_message("Viewing", issue),
        "blocks": blocks,
    }


def command_assign(connection, mapping, actor, token, rest):
    ref, who = split_ref(rest)
    issue = resolve_ref(connection, mapping, ref)
    require_project_member(actor, issue.project)
    target = resolve_member(connection, token, who)
    _, created = IssueAssignee.objects.get_or_create(
        issue=issue,
        assignee_id=target.id,
        defaults={"project_id": issue.project_id, "workspace_id": issue.workspace_id},
    )
    verb = "Assigned" if created else "Already assigned"
    # Reload so the summary shows the new assignee instead of the prefetched set.
    issue = issue_query().get(id=issue.id)
    return {"response_type": "in_channel", "text": issue_message(f"{verb} {member_display(target)} to", issue)}


def command_label(connection, mapping, actor, rest):
    ref, names_text = split_ref(rest)
    issue = resolve_ref(connection, mapping, ref)
    require_project_member(actor, issue.project)
    names = [name.strip() for name in names_text.split(",")]
    # Dedupe case-insensitively while keeping each name's first spelling.
    unique = {}
    for name in names:
        if name:
            unique.setdefault(name.casefold(), name)
    if not unique:
        raise CommandError("Give at least one label name, separated by commas.")
    if len(unique) > MAX_LABELS:
        raise CommandError(f"Up to {MAX_LABELS} labels per command.")
    for name in unique.values():
        label = Label.objects.filter(project=issue.project, name__iexact=name).first()
        if label is None:
            label = Label.objects.create(
                name=name[:255],
                project=issue.project,
                workspace=issue.project.workspace,
            )
        # The through row carries project/workspace; plain labels.add() cannot set them.
        IssueLabel.objects.get_or_create(
            issue=issue,
            label_id=label.id,
            defaults={"project_id": issue.project_id, "workspace_id": issue.workspace_id},
        )
    # Reload so the summary shows the new labels instead of the prefetched set.
    issue = issue_query().get(id=issue.id)
    return {"response_type": "in_channel", "text": issue_message("Labeled", issue)}


def command_state(connection, mapping, actor, rest):
    ref, name = split_ref(rest)
    if not name:
        raise CommandError("Move to which state? Give the state name.")
    issue = resolve_ref(connection, mapping, ref)
    require_project_member(actor, issue.project)
    state = State.objects.filter(project=issue.project, name__iexact=name.strip()).first()
    if state is None:
        raise CommandError(f"No state named {slack_escape(name.strip())} exists in project {issue.project.identifier}.")
    issue.state = state
    issue.save(update_fields=["state", "updated_at"])
    return {"response_type": "in_channel", "text": issue_message("Moved", issue)}


def command_comment(connection, mapping, actor, rest):
    ref, text = split_ref(rest)
    if not text:
        raise CommandError("Comment with what? Give the comment text.")
    issue = resolve_ref(connection, mapping, ref)
    require_project_member(actor, issue.project)
    IssueComment.objects.create(
        project=issue.project,
        workspace=issue.project.workspace,
        issue=issue,
        comment_html=f"<p>{html.escape(text)}</p>",
    )
    return {"response_type": "in_channel", "text": issue_message("Commented on", issue)}


def command_close(connection, mapping, actor, rest):
    ref, extra = split_ref(rest)
    if extra:
        raise CommandError("Close takes a single work item reference.")
    issue = resolve_ref(connection, mapping, ref)
    require_project_member(actor, issue.project)
    completed = (
        State.objects.filter(project=issue.project, group="completed").order_by("-default", "sequence").first()
    )
    if completed is None:
        raise CommandError(f"Project {issue.project.identifier} has no completed state.")
    issue.state = completed
    issue.save(update_fields=["state", "updated_at"])
    return {"response_type": "in_channel", "text": issue_message("Closed", issue)}


def command_list(connection, mapping, actor, rest):
    name = rest.strip()
    issues = (
        issue_query()
        .filter(project=mapping.project)
        .order_by("-created_at")
    )
    state = None
    if name:
        state = State.objects.filter(project=mapping.project, name__iexact=name).first()
        if state is None:
            raise CommandError(f"No state named {slack_escape(name)} exists in project {mapping.project.identifier}.")
        issues = issues.filter(state=state)
    else:
        issues = issues.exclude(state__group="completed")
    issues = list(issues[:LIST_LIMIT])
    base = configuration()["BASE_URL"].rstrip("/")
    header = f"{mapping.project.identifier} · {state.name if state else 'open issues'}"
    if not issues:
        return {
            "response_type": "in_channel",
            "text": f"{header}\nNo issues.",
            "blocks": [
                {"type": "section", "text": {"type": "mrkdwn", "text": slack_escape(header)}},
                {"type": "context", "elements": [{"type": "mrkdwn", "text": "No issues."}]},
            ],
        }
    lines = [header]
    for issue in issues:
        state = slack_escape(issue.state.name) if issue.state else "none"
        lines.append(f"{issue_key(issue)} {slack_escape(issue.name)} — {state}")
    lines.append(f"{base}/{mapping.project.workspace.slug}/projects/{mapping.project_id}")
    return {
        "response_type": "in_channel",
        "text": "\n".join(lines),
        "blocks": slack_blocks_list(header, issues, base_url=base),
    }


def execute(payload):
    """Run one validated slash-command payload and build the Slack response."""
    team_id = services.slack_id(payload.get("team_id"))
    channel_id = services.slack_id(payload.get("channel_id"))
    user_id = services.slack_id(payload.get("user_id"))
    text = payload.get("text")
    if not isinstance(text, str):
        text = ""
    parsed = parse(text)
    action, rest = parsed["action"], parsed["rest"]
    if action == "help":
        return {"response_type": "ephemeral", "text": HELP_TEXT}
    connection = active_connection(team_id)
    token = bot_token(connection)
    mapping = channel_mapping(connection, channel_id)
    actor = actor_user(connection, token, user_id)
    # Attribute every write of this command to the Slack actor; BaseModel
    # derives created_by from the current request user, which is absent here.
    with impersonate(actor):
        return dispatch(connection, mapping, actor, token, action, rest)


def dispatch(connection, mapping, actor, token, action, rest):
    if action == "create":
        require_mapping(mapping)
        require_project_member(actor, mapping.project)
        return command_create(connection, mapping, actor, rest)
    if action == "list":
        require_mapping(mapping)
        require_project_member(actor, mapping.project)
        return command_list(connection, mapping, actor, rest)
    if action == "view":
        return command_view(connection, mapping, actor, rest)
    if action == "lookup":
        # `/plane DEV-12` — same lookup as view, but answered only to the actor.
        return command_lookup(connection, mapping, actor, rest)
    if action == "assign":
        return command_assign(connection, mapping, actor, token, rest)
    if action == "label":
        return command_label(connection, mapping, actor, rest)
    if action == "state":
        return command_state(connection, mapping, actor, rest)
    if action == "comment":
        return command_comment(connection, mapping, actor, rest)
    if action == "close":
        return command_close(connection, mapping, actor, rest)
    raise CommandError("Unknown command.")


def command_lookup(connection, mapping, actor, rest):
    """/plane KEY-12 (or a bare number) — quick lookup like `/jira KEY`: the
    card is answered ephemerally, visible only to the actor."""
    ref, extra = split_ref(rest)
    if extra:
        raise CommandError("Lookups take a single work item reference.")
    issue = resolve_ref(connection, mapping, ref)
    require_project_member(actor, issue.project)
    blocks, _ = slack_blocks_issue(issue)
    return {
        "response_type": "ephemeral",
        "text": issue_message("Viewing", issue),
        "blocks": blocks,
    }
