# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only

import logging

from plane.db.models import Issue
from . import entities, services
from .client import SlackClient, SlackUnavailable, bot_token, slack_blocks_base_url, slack_blocks_issue
from .commands import issue_for_browse_url, issue_url_ids

logger = logging.getLogger(__name__)

# Slack caps chat.unfurl payloads at 5 links per event; the bound also keeps
# lookups cheap on hostile events.
MAX_LINKS = 20


def build_unfurls(connection, links):
    """Map board issue URLs in a link_shared event to unfurl payloads.

    Each entry carries the Work Object entity (the Jira-style card) and the
    plain blocks card kept as a fallback for workspaces without Work Object
    Previews.
    """
    payloads = {}
    base_url = slack_blocks_base_url()
    for link in links[:MAX_LINKS]:
        if not isinstance(link, dict):
            continue
        url = link.get("url")
        if not isinstance(url, str) or len(url) > 2000:
            continue
        ids = issue_url_ids(url)
        if ids:
            # The ids, not the host, decide access: only issues of the workspace
            # this Slack workspace is connected to are ever revealed.
            issue = (
                Issue.objects.select_related("project", "project__workspace", "state")
                .prefetch_related("labels")
                .filter(id=ids[1], project_id=ids[0], project__workspace_id=connection.workspace_id)
                .first()
            )
        else:
            # /<slug>/browse/KEY-12 short links resolve inside the connected
            # workspace only.
            issue = issue_for_browse_url(connection, url)
        if issue is None:
            continue
        blocks, fallback = slack_blocks_issue(issue, base_url=base_url, link_url=url)
        payloads[url] = {
            "entity": entities.entity_for_issue(issue, base_url=base_url, app_unfurl_url=url),
            "preview": entities.composer_preview(issue, base_url=base_url),
            "blocks": blocks,
            "fallback": fallback,
        }
    return payloads


def process_unfurl(connection, event):
    """Unfurl Plane issue links of one link_shared event; skip silently otherwise."""
    links = event.get("links")
    if not isinstance(links, list):
        return
    payloads = build_unfurls(connection, links)
    if not payloads:
        return
    client = SlackClient()
    token = bot_token(connection)
    channel = event.get("channel")
    if channel == "COMPOSER":
        # Composer previews arrive without a real conversation; chat.unfurl
        # takes unfurl_id + source for those instead of channel + ts. They
        # render the compact preview card, never the entity.
        unfurl_id = event.get("unfurl_id")
        source = event.get("source")
        if not isinstance(unfurl_id, str) or not unfurl_id or not isinstance(source, str) or not source:
            return
        unfurls = {url: {"preview": payload["preview"], "fallback": payload["fallback"]} for url, payload in payloads.items()}
        client.unfurl(token, None, None, unfurls, unfurl_id=unfurl_id, source=source)
        return
    channel = services.slack_id(channel)
    ts = services.message_ts(event.get("message_ts") or event.get("ts"))
    try:
        client.unfurl(token, channel, ts, None, metadata={"entities": [payload["entity"] for payload in payloads.values()]})
        return
    except SlackUnavailable as error:
        # Work Object Previews disabled, or metadata rejected: the plain
        # blocks card still renders. The metadata failure is logged once and
        # never retried with metadata again for this event.
        logger.info("slack unfurl metadata rejected, using blocks fallback: %s", error)
    blocks = {url: {"blocks": payload["blocks"], "fallback": payload["fallback"]} for url, payload in payloads.items()}
    client.unfurl(token, channel, ts, blocks)


def present_details(connection, event):
    """Open the Work Object flexpane for one entity_details_requested event.

    Runs inline in the webhook view: a trigger_id stays valid only briefly,
    so a celery hop risks invalid_trigger_id. Workspace-scoped like the card
    itself — anyone who can see the unfurl can already read its fields.
    """
    import uuid as uuid_module

    trigger_id = event.get("trigger_id")
    if not isinstance(trigger_id, str) or not 1 <= len(trigger_id) <= 255:
        return False
    ref = event.get("external_ref")
    if not isinstance(ref, dict):
        return False
    try:
        issue_id = uuid_module.UUID(str(ref.get("id")))
    except (ValueError, TypeError, AttributeError):
        return False
    issue = (
        Issue.objects.select_related("project", "project__workspace", "state")
        .prefetch_related("labels")
        .filter(id=issue_id, project__workspace_id=connection.workspace_id)
        .first()
    )
    client = SlackClient()
    token = bot_token(connection)
    if issue is None:
        client.present_details(token, trigger_id, None, error={"status": "not_found"})
        return True
    client.present_details(token, trigger_id, entities.details_metadata(issue))
    return True
