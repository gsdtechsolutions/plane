# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only

from plane.db.models import Issue
from . import services
from .client import SlackClient, bot_token, slack_blocks_base_url, slack_blocks_issue
from .commands import issue_url_ids

# Slack caps chat.unfurl payloads at 5 links per event; the bound also keeps
# lookups cheap on hostile events.
MAX_LINKS = 20


def build_unfurls(connection, links):
    """Map board issue URLs in a link_shared event to rich unfurl cards."""
    unfurls = {}
    base_url = slack_blocks_base_url()
    for link in links[:MAX_LINKS]:
        if not isinstance(link, dict):
            continue
        url = link.get("url")
        if not isinstance(url, str) or len(url) > 2000:
            continue
        ids = issue_url_ids(url)
        if not ids:
            continue
        # The ids, not the host, decide access: only issues of the workspace
        # this Slack workspace is connected to are ever revealed.
        issue = (
            Issue.objects.select_related("project", "project__workspace", "state")
            .prefetch_related("assignees", "labels")
            .filter(id=ids[1], project_id=ids[0], project__workspace_id=connection.workspace_id)
            .first()
        )
        if issue is None:
            continue
        # chat.unfurl accepts the same block fields as a message per link; the
        # plain-text fallback covers clients that do not render blocks. When no
        # board origin is configured, the card links to the shared URL itself.
        blocks, fallback = slack_blocks_issue(issue, base_url=base_url, link_url=url)
        unfurls[url] = {"blocks": blocks, "fallback": fallback}
    return unfurls


def process_unfurl(connection, event):
    """Unfurl Plane issue links of one link_shared event; skip silently otherwise."""
    channel = services.slack_id(event.get("channel"))
    ts = services.message_ts(event.get("message_ts") or event.get("ts"))
    links = event.get("links")
    if not isinstance(links, list):
        return
    unfurls = build_unfurls(connection, links)
    if not unfurls:
        return
    SlackClient().unfurl(bot_token(connection), channel, ts, unfurls)
