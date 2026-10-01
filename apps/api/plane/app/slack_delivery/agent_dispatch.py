# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only

"""Agent dispatch: send a Plane work item to a coding agent from Slack.

Plane owns the Slack surface (see SPECS/agent-dispatch-connector.md): the
dispatch button and slash command, the thread that carries the conversation,
and the rendering of dispatcher events. The dispatcher (agent-dispatch on the
harness) never talks to Slack; every human-facing line in the thread is
posted here. Configuration is env-only because the dispatcher address is a
tailnet host that is assigned at deploy time.
"""

import hashlib
import hmac
import html
import json
import logging
import os
import re
import time
import uuid
from datetime import timedelta
from urllib.parse import urlsplit

import requests
from crum import impersonate
from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Case, IntegerField, Value, When
from django.utils import timezone

from plane.db.models import IssueComment, SlackEventDelivery, State
from plane.db.models.slack_delivery import SlackAgentJob
from . import commands
from .client import (
    SlackClient,
    SlackUnavailable,
    bot_token,
    slack_blocks_escape,
)

logger = logging.getLogger(__name__)

DISPATCH_TIMEOUT = (5, 20)
EVENTS_MAX_SKEW = 300  # reject signed events older/newer than five minutes
EVENT_BODY_MAX = 1_048_576
TEXT_MAX = 3000  # Slack entity/message text cap
INSTRUCTIONS_MAX = 4000
OPTION_BUTTONS_MAX = 5
BUTTON_TEXT_MAX = 75
ACTION_VALUE_MAX = 2000
JOB_ID_RE = re.compile(r"[A-Za-z0-9._-]{1,64}")
EVENT_ID_RE = re.compile(r"[A-Za-z0-9._:-]{4,64}")
EVENT_TYPES = (
    "job.routed",
    "job.started",
    "job.progress",
    "job.question",
    "job.preview_ready",
    "job.completed",
    "job.failed",
    "job.cancelled",
)
ACTIVE_STATUSES = ("dispatching", "active")
TERMINAL_STATUSES = ("completed", "failed", "cancelled")
# "Review-type state": a state whose name contains the standalone word
# "review" (Review, In review, Code review) — not Preview/Reviewed-by.
REVIEW_STATE_RE = r"(^|[^a-zA-Z])review([^a-zA-Z]|$)"

ACTION_DISPATCH = "plane:dispatch-agent"
ACTION_ANSWER = "plane:agent-answer"
ACTION_CANCEL = "plane:agent-cancel"
ACTION_PREVIEW = "plane:agent-preview"

# Watchdog: a link stuck in `dispatching` means its celery task died before
# the anchor/job-creation finished; the task is safe to re-run (idempotent).
DISPATCH_REENQUEUE_AFTER = 5 * 60
DISPATCH_FAIL_AFTER = 30 * 60
RECOVERY_LIMIT = 20


class DispatchUnavailable(Exception):
    """The dispatcher is unreachable or rejected a call; user-visible in acks."""


class DispatchConflict(DispatchUnavailable):
    """The dispatcher answered 409: the job already finished."""


JOB_FINISHED_TEXT = "That agent run already finished."


def dispatch_config():
    """Env-only configuration; settings attributes exist for tests/overrides."""
    return {
        "URL": (
            str(getattr(settings, "AGENT_DISPATCH_URL", "") or os.environ.get("AGENT_DISPATCH_URL", ""))
            .rstrip("/")
            .rstrip("/")
        ),
        "TOKEN": str(getattr(settings, "AGENT_DISPATCH_TOKEN", "") or os.environ.get("AGENT_DISPATCH_TOKEN", "")),
        "EVENTS_SECRET": str(
            getattr(settings, "AGENT_DISPATCH_EVENTS_SECRET", "") or os.environ.get("AGENT_DISPATCH_EVENTS_SECRET", "")
        ),
    }


def require_dispatch_config():
    config = dispatch_config()
    if not config["URL"] or not config["TOKEN"]:
        raise commands.CommandError(
            "Agent dispatch is not configured yet. An admin must set AGENT_DISPATCH_URL and AGENT_DISPATCH_TOKEN."
        )
    return config


def valid_dispatch_base(base):
    parsed = urlsplit(base or "")
    return (
        parsed.scheme in ("http", "https")
        and bool(parsed.hostname)
        and not parsed.username
        and not parsed.password
        and not parsed.query
        and not parsed.fragment
        and parsed.path in ("", "/")
    )


def dispatcher_post(path, payload):
    """POST one JSON call to the dispatcher; never follows redirects."""
    if not isinstance(path, str) or not path.startswith("/") or path.startswith("//"):
        raise DispatchUnavailable("Invalid dispatcher path.")
    config = dispatch_config()
    if not valid_dispatch_base(config["URL"]) or not config["TOKEN"]:
        raise DispatchUnavailable("Agent dispatch is not configured.")
    try:
        response = requests.post(
            config["URL"].rstrip("/") + path,
            json=payload,
            headers={"Authorization": f"Bearer {config['TOKEN']}"},
            timeout=DISPATCH_TIMEOUT,
            allow_redirects=False,
        )
    except requests.RequestException as error:
        raise DispatchUnavailable(f"Agent dispatch unreachable: {type(error).__name__}") from error
    if response.status_code == 409:
        # Dispatcher contract: 409 on /messages//cancel means the job is
        # finished (never retried, and not a transport problem).
        raise DispatchConflict(JOB_FINISHED_TEXT)
    if not 200 <= response.status_code < 300:
        raise DispatchUnavailable(f"Agent dispatch returned HTTP {response.status_code}.")
    try:
        result = response.json()
    except (ValueError, AttributeError) as error:
        raise DispatchUnavailable("Agent dispatch returned invalid JSON.") from error
    if not isinstance(result, dict):
        raise DispatchUnavailable("Agent dispatch returned invalid JSON.")
    return result


def checked_job_id(value):
    value = str(value or "")
    if not JOB_ID_RE.fullmatch(value):
        raise DispatchUnavailable("Agent dispatch returned an invalid job id.")
    return value


def routing_config():
    """Routing handed to the dispatcher: auto (Jev) by default, with an
    env-level override (AGENT_DISPATCH_ROUTING as JSON, e.g. manual
    harness/cwd pinning while routing LLM access is being sorted out).
    Per-dispatch routing from Slack is a later feature."""
    raw = str(
        getattr(settings, "AGENT_DISPATCH_ROUTING", "") or os.environ.get("AGENT_DISPATCH_ROUTING", "")
    ).strip()
    if raw:
        try:
            parsed = json.loads(raw)
        except ValueError:
            parsed = None
        if isinstance(parsed, dict) and parsed.get("mode") in ("auto", "manual"):
            return parsed
        logger.warning("AGENT_DISPATCH_ROUTING is not a valid routing object; using auto")
    return {"mode": "auto"}


def create_job(payload):
    result = dispatcher_post("/v1/jobs", payload)
    return checked_job_id(result.get("job_id")), str(result.get("status") or "queued")


def post_job_message(job_id, body):
    return dispatcher_post(f"/v1/jobs/{checked_job_id(job_id)}/messages", body)


def cancel_job(job_id):
    return dispatcher_post(f"/v1/jobs/{checked_job_id(job_id)}/cancel", {})


# --- shared text helpers (mrkdwn-safe) ---


def _clip(value, limit):
    value = str(value or "")
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


def _line(value):
    return " ".join(str(value or "").split())


def board_url(issue):
    try:
        return commands.board_link(issue)
    except Exception:
        return ""


def cancel_button(link):
    return {
        "type": "button",
        "text": {"type": "plain_text", "text": "Cancel", "emoji": True},
        "style": "danger",
        "action_id": ACTION_CANCEL,
        "value": json.dumps({"j": str(link.id)})[:ACTION_VALUE_MAX],
    }


# --- dispatch entry points ---


def register_job(
    *,
    connection,
    issue,
    team_id,
    channel_id,
    actor_slack_user_id,
    actor,
    instructions,
    idempotency_key,
):
    """Create (or find) the link row for one dispatch and enqueue the worker.

    The row exists before the worker runs so Slack retries carrying the same
    idempotency key collapse on the unique constraint instead of creating a
    second dispatcher job.
    """
    require_dispatch_config()
    key = _line(idempotency_key)[:200] or f"link-{uuid.uuid4().hex}"
    existing = SlackAgentJob.objects.filter(idempotency_key=key).first()
    if existing is not None:
        return existing, False
    try:
        link = SlackAgentJob.objects.create(
            connection=connection,
            issue=issue,
            team_id=team_id,
            channel_id=channel_id,
            requester_slack_user_id=actor_slack_user_id,
            requester=actor,
            instructions=_line(str(instructions or ""))[:INSTRUCTIONS_MAX],
            idempotency_key=key,
            status="dispatching",
        )
    except IntegrityError:
        return SlackAgentJob.objects.filter(idempotency_key=key).first(), False
    from .tasks import run_agent_dispatch

    transaction.on_commit(lambda: run_agent_dispatch.delay(str(link.id)))
    return link, True


def start_from_command(connection, mapping, actor, token, payload, rest):
    """/plane dispatch <ref> [instructions] — validated in the 3s window,
    anchored and sent by the celery task."""
    ref, instructions = commands.split_ref(rest)
    issue = commands.resolve_ref(connection, mapping, ref)
    commands.require_project_member(actor, issue.project)
    link, created = register_job(
        connection=connection,
        issue=issue,
        team_id=payload.get("team_id", ""),
        channel_id=payload.get("channel_id", ""),
        actor_slack_user_id=payload.get("user_id", ""),
        actor=actor,
        instructions=instructions,
        idempotency_key=payload.get("trigger_id", ""),
    )
    key = commands.issue_key(issue)
    if not created:
        return {
            "response_type": "ephemeral",
            "text": f"An agent run for {key} is already being dispatched from this action.",
        }
    return {
        "response_type": "ephemeral",
        "text": f"🤖 Dispatching {key} · {slack_blocks_escape(issue.name)} to a coding agent — updates will land in the thread I post next.",
    }


def start_from_button(connection, actor, issue, parsed):
    """Block-actions "🤖 Dispatch to agent" on a Work Object card."""
    link, created = register_job(
        connection=connection,
        issue=issue,
        team_id=parsed["team_id"],
        channel_id=parsed["channel_id"],
        actor_slack_user_id=parsed["user_id"],
        actor=actor,
        instructions="",
        idempotency_key=parsed.get("action_ts", ""),
    )
    key = commands.issue_key(issue)
    if not created:
        return {
            "response_type": "ephemeral",
            "text": f"An agent run for {key} is already being dispatched from this action.",
        }
    return {
        "response_type": "ephemeral",
        "text": f"🤖 Dispatching {key} · {slack_blocks_escape(issue.name)} to a coding agent — updates will land in the thread I post next.",
    }


def allowed_reply_user(connection, link, user_id, actor=None):
    """The requester or an active member of the issue's project; None means
    the stranger is ignored silently. `actor` is an already-resolved member
    candidate (saves a Slack users.info call)."""
    if user_id and user_id == link.requester_slack_user_id:
        return link.requester
    if actor is not None:
        try:
            commands.require_project_member(actor, link.issue.project)
            return actor
        except commands.CommandError:
            return None
    try:
        token = bot_token(connection)
        actor = commands.actor_user(connection, token, user_id)
        commands.require_project_member(actor, link.issue.project)
        return actor
    except (commands.CommandError, SlackUnavailable):
        return None


def _agent_button_value(action):
    try:
        value = json.loads(str(action.get("value") or ""))
    except (ValueError, TypeError):
        return None
    if not isinstance(value, dict) or not value.get("j"):
        return None
    return value


def agent_button(connection, parsed, action):
    """Answer/Cancel/preview buttons rendered into the dispatch thread.

    Strangers (not the requester, not a project member) are ignored silently
    per the connector spec; members and the requester get ephemeral acks.
    """
    value = _agent_button_value(action)
    if value is None:
        return {"response_type": "ephemeral", "text": "That button is no longer available."}
    link = (
        SlackAgentJob.objects.select_related("issue", "issue__project", "connection")
        .filter(id=str(value.get("j"))[:36], channel_id=parsed["channel_id"])
        .first()
    )
    if link is None:
        return {"response_type": "ephemeral", "text": "That agent run is gone."}
    plane_user = allowed_reply_user(connection, link, parsed["user_id"])
    if plane_user is None:
        return None  # silently ignored
    if action.get("action_id") == ACTION_PREVIEW:
        # Preview links outlive the run (~30 min after completion), so this
        # one button stays usable in terminal states.
        text = _preview_text(link.preview)
        if not text:
            return {"response_type": "ephemeral", "text": "No preview was registered for this run."}
        try:
            SlackClient().post_ephemeral(
                bot_token(link.connection), link.channel_id, parsed["user_id"], text, thread_ts=link.thread_ts
            )
        except SlackUnavailable as error:
            logger.warning("agent preview for job %s failed: %s", link.job_id, error)
            return {"response_type": "ephemeral", "text": "Plane could not post the links. Try again."}
        return None  # links were delivered ephemerally; no further ack
    if link.status in TERMINAL_STATUSES:
        return {"response_type": "ephemeral", "text": JOB_FINISHED_TEXT}
    if action.get("action_id") == ACTION_CANCEL:
        if link.job_id:
            try:
                cancel_job(link.job_id)
            except DispatchConflict:
                # The beat us to it: the dispatcher already finished the job.
                _mark_completed_from_conflict(link)
                return {"response_type": "ephemeral", "text": JOB_FINISHED_TEXT}
            except DispatchUnavailable as error:
                logger.warning("agent cancel failed for job %s: %s", link.job_id, error)
                return {"response_type": "ephemeral", "text": "Plane could not reach the dispatcher. Try again."}
        link.status = "cancelled"
        link.save(update_fields=["status", "updated_at"])
        return {"response_type": "ephemeral", "text": "🛑 Cancel sent — the agent will stop."}
    # ACTION_ANSWER: forward the picked option as the answer to the question.
    question_id = str(value.get("q") or "")[:64]
    option = _line(value.get("o"))[:500]
    if not option:
        return {"response_type": "ephemeral", "text": "That option is no longer available."}
    try:
        post_job_message(
            link.job_id,
            {
                "question_id": question_id,
                "slack_user_id": parsed["user_id"],
                "plane_user_id": str(plane_user.id),
                "text": option,
                "ts": parsed.get("action_ts", ""),
            },
        )
    except DispatchConflict:
        _mark_completed_from_conflict(link)
        return {"response_type": "ephemeral", "text": JOB_FINISHED_TEXT}
    except DispatchUnavailable as error:
        logger.warning("agent answer failed for job %s: %s", link.job_id, error)
        return {"response_type": "ephemeral", "text": "Plane could not reach the dispatcher. Try again."}
    return {"response_type": "ephemeral", "text": f"✅ Answer sent: {slack_blocks_escape(option)}"}


def _mark_completed_from_conflict(link):
    """A 409 from the dispatcher means the job finished without telling us."""
    if link.status not in TERMINAL_STATUSES:
        link.status = "completed"
        link.save(update_fields=["status", "updated_at"])


# --- celery worker: anchor + job creation ---


def _anchor_blocks(link):
    key = commands.issue_key(link.issue)
    head = f"🤖 Dispatching *{key} · {_clip(slack_blocks_escape(link.issue.name), 120)}* to a coding agent."
    instructions = _line(link.instructions)
    if instructions:
        head += f"\n>_{_clip(slack_blocks_escape(instructions), 500)}_"
    url = board_url(link.issue)
    context = "Agent updates land in this thread."
    if url:
        context += f" · {url}"
    return (
        [
            {"type": "section", "text": {"type": "mrkdwn", "text": head[:TEXT_MAX]}},
            {"type": "actions", "elements": [cancel_button(link)]},
            {"type": "context", "elements": [{"type": "mrkdwn", "text": _clip(context, TEXT_MAX)}]},
        ],
        f"Dispatching {key} to a coding agent.",
    )


def dispatch_link(link_id):
    """Anchor the thread and hand the job to the dispatcher.

    Runs in a celery task (autocommit), so the read-modify-write cycle is
    wrapped in one transaction — select_for_update needs it, and it also
    serializes concurrent workers. Idempotent across celery retries: the
    anchor posts once (thread_ts is the guard), and POST /v1/jobs dedupes
    server-side on idempotency_key.
    """
    with transaction.atomic():
        link = (
            SlackAgentJob.objects.select_for_update(of=("self",))
            .select_related("connection", "issue", "issue__project", "issue__project__workspace", "requester")
            .get(id=link_id)
        )
        if link.status in TERMINAL_STATUSES:
            return
        if link.thread_ts and link.job_id:
            return
        token = bot_token(link.connection)
        if not link.thread_ts:
            blocks, text = _anchor_blocks(link)
            anchor = SlackClient().post_message(token, link.channel_id, blocks, text)
            ts = anchor.get("ts") if isinstance(anchor, dict) else None
            if not isinstance(ts, str) or not re.fullmatch(r"[0-9]{6,20}(\.[0-9]{1,10})?", ts):
                raise SlackUnavailable("Slack did not return an anchor timestamp.")
            link.thread_ts = ts
            link.save(update_fields=["thread_ts", "updated_at"])
        if not link.job_id:
            issue = link.issue
            display = commands.member_display(link.requester) if link.requester else "unknown member"
            job_id, job_status = create_job(
                {
                    "source": {
                        "kind": "plane",
                        "ticket": commands.issue_key(issue),
                        "issue_id": str(issue.id),
                        "workspace": issue.project.workspace.slug,
                        "project_id": str(issue.project_id),
                        "url": board_url(issue),
                    },
                    "requester": {
                        "slack_team_id": link.team_id,
                        "slack_user_id": link.requester_slack_user_id,
                        "plane_user_id": str(link.requester_id) if link.requester_id else "",
                        "display_name": display,
                    },
                "reply_to": {"channel_id": link.channel_id, "thread_ts": link.thread_ts},
                "routing": routing_config(),
                    "instructions": _line(link.instructions)[:INSTRUCTIONS_MAX],
                    "preview": True,
                    "idempotency_key": link.idempotency_key,
                }
            )
            link.job_id = job_id
            link.status = "active"
            link.save(update_fields=["job_id", "status", "updated_at"])
            SlackClient().post_message(
                token,
                link.channel_id,
                [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": _clip(f"Job `{job_id}` created (status: {slack_blocks_escape(job_status)}).", TEXT_MAX),
                        },
                    }
                ],
                f"Job {job_id} created (status: {job_status}).",
                thread_ts=link.thread_ts,
            )


# --- inbound dispatcher events ---


def verify_event_signature(secret, timestamp, raw, signature):
    """v1=<hex hmac_sha256(secret, ts + "." + body)>; 5-minute skew window."""
    if not re.fullmatch(r"[0-9]{1,20}", timestamp or ""):
        return False
    try:
        stamp = int(timestamp)
    except ValueError:
        return False
    if abs(time.time() - stamp) > EVENTS_MAX_SKEW:
        return False
    expected = (
        "v1="
        + hmac.new(secret.encode(), timestamp.encode() + b"." + (raw or b""), hashlib.sha256).hexdigest()
    )
    return hmac.compare_digest(expected.encode(), (signature or "").encode())


def parse_event(payload):
    """Validate one dispatcher event envelope; raise ValidationError if malformed."""
    from rest_framework.exceptions import ValidationError

    if not isinstance(payload, dict):
        raise ValidationError("Invalid agent event.")
    event_id = payload.get("event_id")
    if not isinstance(event_id, str) or not EVENT_ID_RE.fullmatch(event_id):
        raise ValidationError("Invalid agent event id.")
    etype = payload.get("type")
    if not isinstance(etype, str) or not etype.startswith("job."):
        raise ValidationError("Invalid agent event type.")
    job_id = payload.get("job_id")
    if not isinstance(job_id, str) or not JOB_ID_RE.fullmatch(job_id):
        raise ValidationError("Invalid agent event job id.")
    data = payload.get("data")
    if data is not None and not isinstance(data, dict):
        raise ValidationError("Invalid agent event data.")
    reply_to = payload.get("reply_to")
    if reply_to is not None and not isinstance(reply_to, dict):
        raise ValidationError("Invalid agent event reply_to.")
    return event_id, etype, job_id


def handle_event(delivery_id, payload):
    """Render one deduped dispatcher event; raises on failure so the endpoint
    answers 500 and the dispatcher retries. Returns False for no-ops."""
    with transaction.atomic():
        delivery = SlackEventDelivery.objects.select_for_update().get(id=delivery_id)
        if delivery.status == "processed":
            return False
        try:
            rendered = render_event(payload)
        except Exception as error:
            delivery.status = "failed"
            delivery.error = type(error).__name__[:100]
            delivery.processed_at = timezone.now()
            delivery.save(update_fields=["status", "error", "processed_at"])
            raise
        delivery.status = "processed"
        delivery.error = ""
        delivery.payload = {}
        delivery.processed_at = timezone.now()
        delivery.save(update_fields=["status", "error", "payload", "processed_at"])
        return rendered


def _job_link(job_id):
    return (
        SlackAgentJob.objects.select_related(
            "connection", "issue", "issue__project", "issue__project__workspace", "issue__state", "requester"
        )
        .filter(job_id=str(job_id))
        .first()
    )


def _post_thread(token, link, text, *, event_id="", blocks=None):
    """Post one update into the job thread. client_msg_id=event_id lets Slack
    dedupe a redelivery of the same event (~5-minute window) instead of
    printing the line twice."""
    if blocks is None:
        blocks = [
            {"type": "section", "text": {"type": "mrkdwn", "text": _clip(slack_blocks_escape(text), TEXT_MAX)}}
        ]
    SlackClient().post_message(
        token,
        link.channel_id,
        blocks,
        _clip(text, TEXT_MAX) if text else "",
        thread_ts=link.thread_ts,
        client_msg_id=event_id or None,
    )


def _preview_text(preview, *, heading="Preview links (sent only to you):"):
    """The ephemeral preview-links body; empty when nothing was registered."""
    if not isinstance(preview, dict):
        return ""
    app_url = _line(preview.get("app_url"))
    watch_url = _line(preview.get("watch_url"))
    if not app_url and not watch_url:
        return ""
    lines = [heading]
    if app_url:
        lines.append(f"• App: {slack_blocks_escape(app_url)}")
    if watch_url:
        lines.append(f"• Watch: {slack_blocks_escape(watch_url)}")
    expires = _line(preview.get("expires_at"))
    if expires:
        lines.append(f"(expires {slack_blocks_escape(expires)})")
    return "\n".join(lines)


def _review_state(project):
    return (
        State.objects.filter(project=project, name__iregex=REVIEW_STATE_RE)
        .order_by(
            # Prefer an in-flight review state over anything terminal-ish.
            Case(
                When(group="started", then=Value(0)),
                When(group="unstarted", then=Value(1)),
                default=Value(2),
                output_field=IntegerField(),
            ),
            "sequence",
        )
        .first()
    )


def _render_question(token, link, data, event_id):
    question = _clip(slack_blocks_escape(_line(data.get("text"))), TEXT_MAX)
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": question or "(the agent asked an empty question)"}}]
    options = [_line(option) for option in (data.get("options") or []) if _line(option)]
    question_id = str(data.get("question_id") or "")[:64]
    elements = []
    for option in options[:OPTION_BUTTONS_MAX]:
        elements.append(
            {
                "type": "button",
                "text": {"type": "plain_text", "text": _clip(option, BUTTON_TEXT_MAX), "emoji": True},
                "action_id": ACTION_ANSWER,
                "value": json.dumps({"j": str(link.id), "q": question_id, "o": option[:200]})[:ACTION_VALUE_MAX],
            }
        )
    if elements:
        if len(elements) < len(options):
            elements.append(
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Other…", "emoji": True},
                    "action_id": ACTION_ANSWER,
                    "value": json.dumps({"j": str(link.id), "q": question_id, "o": "see thread reply"})[:ACTION_VALUE_MAX],
                }
            )
        elements.append(cancel_button(link))
        blocks.append({"type": "actions", "elements": elements})
    else:
        blocks.append({"type": "actions", "elements": [cancel_button(link)]})
    context = "Reply in this thread to answer."
    timeout_at = _line(data.get("timeout_at"))
    if timeout_at:
        context += f" Until {slack_blocks_escape(timeout_at)}."
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": _clip(context, TEXT_MAX)}]})
    _post_thread(token, link, question or "The agent has a question.", event_id=event_id, blocks=blocks)


def _render_completed(token, link, data, event_id):
    """DB side effects first (each idempotent), Slack post last — a retry
    after a partial failure never duplicates the comment or the state move."""
    summary = _line(data.get("summary"))
    branch = _line(data.get("branch"))
    commits = [str(item).strip() for item in (data.get("commits") or []) if str(item).strip()][:20]
    issue = link.issue
    review = _review_state(issue.project)
    moved = ""
    if review is not None and issue.state_id != review.id:
        issue.state = review
        issue.save(update_fields=["state", "updated_at"])
        moved = f" Moved {commands.issue_key(issue)} to {review.name}."
    marker = f"agent-event:{event_id}"
    if not IssueComment.objects.filter(issue=issue, comment_html__contains=marker).exists():
        summary_html = html.escape(summary).replace("\n", "<br>") if summary else ""
        body = "<p>🤖 Agent run completed.</p>"
        if summary_html:
            body += f"<p>{summary_html}</p>"
        if branch:
            body += f"<p>Branch: {html.escape(branch)}</p>"
        if commits:
            body += "<ul>" + "".join(f"<li>{html.escape(item)}</li>" for item in commits) + "</ul>"
        # Invisible dedupe marker so a redelivery never double-comments.
        body += f"<!--{marker}-->"
        with impersonate(link.requester):
            IssueComment.objects.create(
                project=issue.project,
                workspace=issue.project.workspace,
                issue=issue,
                comment_html=body,
            )
    thread_text = "✅ Agent finished." if not summary else f"✅ Agent finished: {summary}"
    if branch:
        thread_text += f" (branch {branch})"
    if moved:
        thread_text += moved
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": _clip(slack_blocks_escape(thread_text), TEXT_MAX)}}]
    if commits:
        commit_lines = "\n".join(f"• {slack_blocks_escape(item)}" for item in commits)
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": _clip(commit_lines, TEXT_MAX)}})
    _post_thread(token, link, thread_text, event_id=event_id, blocks=blocks)


def render_event(payload):
    """Apply one dispatcher event to its thread (and the issue for completed)."""
    link = _job_link(payload.get("job_id"))
    if link is None:
        logger.info("agent event %s for unknown job %s", payload.get("event_id"), payload.get("job_id"))
        return False
    data = payload.get("data") or {}
    etype = payload.get("type")
    event_id = str(payload.get("event_id") or "")
    token = None
    if link.connection is not None and link.connection.is_active and link.thread_ts:
        try:
            token = bot_token(link.connection)
        except SlackUnavailable:
            token = None
    if etype == "job.routed":
        if token:
            parts = ["Routed to " + slack_blocks_escape(_line(data.get("harness")) or "auto")]
            if _line(data.get("model")):
                parts.append(slack_blocks_escape(_line(data.get("model"))))
            if _line(data.get("folder")):
                parts.append("folder " + slack_blocks_escape(_line(data.get("folder"))))
            if data.get("complexity") is not None:
                parts.append("complexity " + slack_blocks_escape(str(data.get("complexity"))))
            _post_thread(token, link, "🧭 " + " · ".join(parts), event_id=event_id)
    elif etype == "job.started":
        if token:
            line = "🚀 Agent working"
            if _line(data.get("branch")):
                line += f" — branch {slack_blocks_escape(_line(data.get('branch')))}"
            _post_thread(token, link, line, event_id=event_id)
    elif etype == "job.progress":
        if token and _line(data.get("text")):
            _post_thread(token, link, _line(data.get("text")), event_id=event_id)
    elif etype == "job.question":
        if token:
            _render_question(token, link, data, event_id)
    elif etype == "job.preview_ready":
        if token:
            # Register the links for the thread button, ephemeral them to the
            # requester, and post an in-thread notice with the button so
            # project members can self-serve without the links broadcasting.
            link.preview = {
                "app_url": _line(data.get("app_url")),
                "watch_url": _line(data.get("watch_url")),
                "expires_at": _line(data.get("expires_at")),
            }
            link.save(update_fields=["preview", "updated_at"])
            if link.requester_slack_user_id:
                requester_text = _preview_text(link.preview, heading="🔗 Preview is ready (sent only to you):")
                if requester_text:
                    SlackClient().post_ephemeral(
                        token,
                        link.channel_id,
                        link.requester_slack_user_id,
                        requester_text,
                        thread_ts=link.thread_ts,
                    )
            _post_thread(
                token,
                link,
                "🔗 Preview is ready — project members can fetch the links with the button below.",
                event_id=event_id,
                blocks=[
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": _clip("🔗 *Preview is ready.* Requester got the links; other project members:", TEXT_MAX),
                        },
                    },
                    {
                        "type": "actions",
                        "elements": [
                            {
                                "type": "button",
                                "text": {"type": "plain_text", "text": "Get preview links", "emoji": True},
                                "action_id": ACTION_PREVIEW,
                                "value": json.dumps({"j": str(link.id)})[:ACTION_VALUE_MAX],
                            }
                        ],
                    },
                ],
            )
    elif etype == "job.completed":
        if token:
            _render_completed(token, link, data, event_id)
        link.status = "completed"
    elif etype == "job.failed":
        if token:
            reason = _line(data.get("reason")) or "No reason given."
            _post_thread(token, link, f"⚠️ Agent run failed: {reason}", event_id=event_id)
        link.status = "failed"
    elif etype == "job.cancelled":
        if token:
            reason = _line(data.get("reason")) or "No reason given."
            _post_thread(token, link, f"🛑 Agent run cancelled: {reason}", event_id=event_id)
        link.status = "cancelled"
    else:
        return False
    link.last_event_id = str(payload.get("event_id") or "")[:64]
    link.save(update_fields=["status", "last_event_id", "updated_at"])
    return True


# --- thread replies → dispatcher ---


def route_thread_reply(connection, event):
    """Forward human replies in a mapped dispatch thread to the dispatcher.

    Hooked from services.apply_delivery for every message event (the channel
    may have no work-item mapping at all). Strangers are ignored silently.
    """
    if not isinstance(event, dict):
        return
    if event.get("subtype") in ("message_deleted", "message_changed", "bot_message"):
        return
    if event.get("bot_id"):
        return
    user_id = event.get("user")
    if not isinstance(user_id, str) or not re.fullmatch(r"[A-Z][A-Z0-9]{5,30}", user_id):
        return
    if user_id == connection.bot_user_id:
        return
    thread_ts = event.get("thread_ts")
    ts = event.get("ts")
    if not isinstance(thread_ts, str) or not thread_ts or thread_ts == ts:
        return  # not a thread reply
    channel = event.get("channel")
    if not isinstance(channel, str) or not channel:
        return
    text = event.get("text")
    if not isinstance(text, str) or not text.strip():
        return
    link = (
        SlackAgentJob.objects.select_related("issue", "issue__project", "connection", "requester")
        .filter(connection=connection, channel_id=channel, thread_ts=thread_ts)
        .first()
    )
    if link is None or not link.job_id:
        return
    reply_ts = ts if isinstance(ts, str) and re.fullmatch(r"[0-9]{6,20}(\.[0-9]{1,10})?", ts) else ""
    # Awaiting-mapping redeliveries re-run this hook for old messages; the
    # cursor skips anything the dispatcher already received. Slack ts values
    # are zero-padded, so lexicographic order matches time order.
    if reply_ts and link.last_reply_ts and reply_ts <= link.last_reply_ts:
        return
    plane_user = allowed_reply_user(connection, link, user_id)
    if plane_user is None:
        return
    try:
        post_job_message(
            link.job_id,
            {
                "slack_user_id": user_id,
                "plane_user_id": str(plane_user.id) if plane_user else "",
                "text": text[:4000],
                "ts": reply_ts,
            },
        )
    except DispatchConflict:
        # The job finished without emitting its terminal event (yet); stop
        # treating the thread as live. No visible refusal per spec.
        _mark_completed_from_conflict(link)
        return
    except DispatchUnavailable as error:
        # The dispatcher retries events but thread text is push-only; log and
        # let it go rather than failing the whole Slack delivery.
        logger.warning("agent thread reply for job %s failed: %s", link.job_id, error)
        return
    if reply_ts:
        link.last_reply_ts = reply_ts
        link.save(update_fields=["last_reply_ts", "updated_at"])


# --- watchdog: recover dispatch links whose celery task died ---


def recover_stuck_links(
    *,
    reenqueue_after=DISPATCH_REENQUEUE_AFTER,
    fail_after=DISPATCH_FAIL_AFTER,
    limit=RECOVERY_LIMIT,
):
    """Requeue dispatching links whose worker died, fail the ancient ones.

    dispatch_link is idempotent (thread_ts guard + dispatcher-side
    idempotency_key), so a blind re-enqueue cannot duplicate anything. Links
    older than fail_after never came back on their own; marking them failed
    stops the requeue loop and surfaces the loss in the thread.
    """
    from .tasks import run_agent_dispatch

    now = timezone.now()
    stuck = (
        SlackAgentJob.objects.select_related("connection")
        .filter(status="dispatching", created_at__lte=now - timedelta(seconds=reenqueue_after))
        .order_by("created_at")[:limit]
    )
    for link in stuck:
        if now - link.created_at > timedelta(seconds=fail_after):
            link.status = "failed"
            link.save(update_fields=["status", "updated_at"])
            if link.thread_ts and link.connection is not None and link.connection.is_active:
                try:
                    _post_thread(
                        bot_token(link.connection),
                        link,
                        "⚠️ Agent dispatch failed — the job never started. Dispatch it again.",
                        event_id=f"recover-{link.id}",
                    )
                except (SlackUnavailable, DispatchUnavailable):
                    pass
        else:
            run_agent_dispatch.delay(str(link.id))
