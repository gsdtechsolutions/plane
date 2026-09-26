# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import time
from urllib.parse import quote, urlencode, urlsplit

import requests
from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from rest_framework.exceptions import APIException

from plane.db.models.slack_delivery import SlackAppSetup

CONFIG_KEYS = ("CLIENT_ID", "CLIENT_SECRET", "SIGNING_SECRET", "BASE_URL")
BOT_SCOPES = "channels:read,groups:read,channels:history,groups:history,commands,links:read,links:write,users:read,users:read.email"
EVENT_SUBSCRIPTIONS = "message.channels, message.groups, channel_rename, app_uninstalled, tokens_revoked, link_shared"
APP_CREATE_URL = "https://api.slack.com/apps?new_app=1&manifest_json="
SIGNATURE_MAX_SKEW = 300
RESPONSE_URL_PREFIX = "https://hooks.slack.com/"

logger = logging.getLogger(__name__)


class SlackUnavailable(APIException):
    status_code = 503
    default_detail = "Slack could not complete this request. Check the connection and try again."


def environment_key(key):
    return "SLACK_APP_BASE_URL" if key == "BASE_URL" else f"SLACK_{key}"


def app_setup_row(*, lock=False):
    """The instance-wide app credentials row; the most recently updated row wins."""
    query = SlackAppSetup.objects.order_by("-updated_at")
    return query.select_for_update().first() if lock else query.first()


def decrypt_secret(value):
    """Decrypt an admin-entered app secret; unreadable values read as absent
    so credentials can simply be entered again after a SECRET_KEY change."""
    if not isinstance(value, str) or not value:
        return ""
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken:
        return ""


def encrypt_secret(value):
    if not isinstance(value, str) or not value:
        raise SlackUnavailable("Slack app secret is missing.")
    return _fernet().encrypt(value.encode()).decode()


def configuration():
    """Settings overrides win, then the admin-entered database row, then env vars."""
    overrides = getattr(settings, "SLACK_DELIVERY", {})
    row = app_setup_row()
    stored = (
        {
            "CLIENT_ID": row.client_id,
            "CLIENT_SECRET": decrypt_secret(row.client_secret_encrypted),
            "SIGNING_SECRET": decrypt_secret(row.signing_secret_encrypted),
        }
        if row
        else {}
    )
    return {
        key: overrides.get(key) or stored.get(key) or os.environ.get(environment_key(key)) or ""
        for key in CONFIG_KEYS
    }


def valid_origin(origin):
    """Accept public HTTPS origins, and plain HTTP only for local development."""
    if not isinstance(origin, str):
        return False
    parsed = urlsplit(origin)
    localhost = parsed.hostname in ("localhost", "127.0.0.1")
    return (
        (parsed.scheme == "https" or (parsed.scheme == "http" and localhost))
        and bool(parsed.netloc)
        and not parsed.username
        and not parsed.password
        and not parsed.query
        and not parsed.fragment
        and parsed.path in ("", "/")
    )


def board_origin(request):
    """Best-effort public origin of this board, validated before it is used in URLs."""
    override = configuration()["BASE_URL"]
    if valid_origin(override):
        return override.rstrip("/")
    forwarded_host = request.headers.get("X-Forwarded-Host", "")
    host = forwarded_host.split(",")[0].strip() or request.get_host()
    forwarded_proto = request.headers.get("X-Forwarded-Proto", "")
    proto = forwarded_proto.split(",")[0].strip() or request.scheme
    host = host.rstrip(".")
    origin = f"{proto}://{host}"
    return origin if valid_origin(origin) else None


def app_manifest(origin):
    """Slack app manifest pre-filling the click-to-connect app creation form."""
    return {
        "display_information": {
            "name": "Plane",
            "description": "Bring Plane work items into Slack: create, assign and track tickets, and preview Plane links.",
        },
        "features": {
            "bot_user": {"display_name": "Plane", "always_online": False},
            "slash_commands": [
                {
                    "command": "/plane",
                    "url": f"{origin}/api/slack-delivery/commands/",
                    "description": "Manage Plane work items",
                    "should_escape": False,
                }
            ],
            # Slack only delivers link_shared events for registered domains.
            "unfurl_domains": [urlsplit(origin).hostname],
        },
        "oauth_config": {
            "redirect_urls": [f"{origin}/api/slack-delivery/callback/"],
            # team:read lets the callback resolve the workspace slug; the
            # connect-time authorize requests only BOT_SCOPES so apps created
            # from older manifests still install.
            "scopes": {"bot": BOT_SCOPES.split(",") + ["team:read"]},
        },
        "settings": {
            "event_subscriptions": {
                "request_url": f"{origin}/api/slack-delivery/webhooks/",
                "bot_events": EVENT_SUBSCRIPTIONS.split(", "),
            },
            "org_deploy_enabled": False,
        },
    }


def setup_link(origin):
    """Click-to-create Slack app URL carrying the manifest for `origin`."""
    return APP_CREATE_URL + quote(json.dumps(app_manifest(origin)), safe="")


def masked_client_id(client_id):
    return f"{client_id[:9]}…" if len(client_id) > 9 else client_id


def setup_state():
    config = configuration()
    missing = [environment_key(key) for key, value in config.items() if not value]
    valid_url = valid_origin(config["BASE_URL"])
    valid_app = re.fullmatch(r"[0-9]{6,20}\.[0-9]{6,20}", config["CLIENT_ID"] or "") is not None
    base = config["BASE_URL"].rstrip("/") if valid_url else ""
    row = app_setup_row()
    app_configured = bool(
        row
        and row.client_id
        and decrypt_secret(row.client_secret_encrypted)
        and decrypt_secret(row.signing_secret_encrypted)
    )
    return {
        "configured": not missing and bool(valid_url and valid_app),
        "missing_settings": missing,
        "configuration_error": "Use a valid public HTTPS board origin and a Slack app client ID."
        if not missing and not (valid_url and valid_app)
        else None,
        "callback_url": f"{base}/api/slack-delivery/callback/" if base else None,
        "events_url": f"{base}/api/slack-delivery/webhooks/" if base else None,
        "commands_url": f"{base}/api/slack-delivery/commands/" if base else None,
        "scopes": BOT_SCOPES.split(","),
        "event_subscriptions": EVENT_SUBSCRIPTIONS.split(", "),
        "permissions": ["Channels: read history and info", "Groups: read history and info"],
        "manifest": app_manifest(base) if base else None,
        "setup_url": setup_link(base) if base else None,
        "app": {
            "configured": app_configured,
            "client_id_masked": masked_client_id(row.client_id) if row else "",
            "updated_at": row.updated_at.isoformat() if row else None,
        },
    }


def require_configuration():
    if not setup_state()["configured"]:
        raise SlackUnavailable("Slack app is not configured. Ask an instance administrator to complete setup.")
    return configuration()


def verify_signature(secret, timestamp, raw, signature):
    if not re.fullmatch(r"[0-9]{1,20}", timestamp or ""):
        return False
    try:
        request_time = int(timestamp)
    except ValueError:
        return False
    if abs(time.time() - request_time) > SIGNATURE_MAX_SKEW:
        return False
    expected = "v0=" + hmac.new(secret.encode(), f"v0:{timestamp}:".encode() + raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected.encode(), (signature or "").encode())


def _fernet():
    key = base64.urlsafe_b64encode(
        hashlib.pbkdf2_hmac("sha256", settings.SECRET_KEY.encode(), b"plane.slack-delivery.v1", 480_000)
    )
    return Fernet(key)


def encrypt_token(token):
    if not isinstance(token, str) or not token:
        raise SlackUnavailable("Slack did not return a bot token.")
    return _fernet().encrypt(token.encode()).decode()


def bot_token(connection):
    """Decrypt the stored bot token; unreadable tokens force a reconnect."""
    if not connection.bot_token_encrypted:
        raise SlackUnavailable("This Slack connection must be reconnected.")
    try:
        return _fernet().decrypt(connection.bot_token_encrypted.encode()).decode()
    except InvalidToken as error:
        raise SlackUnavailable("This Slack connection must be reconnected.") from error


class SlackClient:
    def __init__(self):
        self.config = require_configuration()

    def _request(self, method, path, token=None, *, data=None):
        base = "https://slack.com/api"
        if not path.startswith("/") or path.startswith("//"):
            raise SlackUnavailable()
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            response = requests.request(
                method, base + path, data=data, headers=headers, timeout=(5, 20), allow_redirects=False
            )
            if not 200 <= response.status_code < 300:
                logger.warning("slack api %s %s -> HTTP %s: %s", method, path, response.status_code, response.text[:200])
                raise SlackUnavailable(f"Slack returned HTTP {response.status_code}.")
            result = response.json()
            if not isinstance(result, dict) or result.get("ok") is not True:
                error = result.get("error", "unknown error") if isinstance(result, dict) else "unparseable response"
                detail = {key: result[key] for key in ("needed", "provided", "response_metadata") if isinstance(result, dict) and result.get(key)}
                logger.warning("slack api %s %s -> %s %s", method, path, error, detail)
                needed = result.get("needed") if isinstance(result, dict) else None
                suffix = f"; needed scope: {needed}" if needed else ""
                raise SlackUnavailable(f"Slack rejected the request ({error}{suffix}).")
            return result
        except (requests.RequestException, ValueError) as error:
            logger.warning("slack api %s %s failed: %s", method, path, error)
            raise SlackUnavailable() from error

    def authorization_url(self, state):
        return "https://slack.com/oauth/v2/authorize?" + urlencode(
            {
                "client_id": self.config["CLIENT_ID"],
                "scope": BOT_SCOPES,
                "state": state,
                "redirect_uri": setup_state()["callback_url"],
            }
        )

    def complete_installation(self, code):
        result = self._request(
            "POST",
            "/oauth.v2.access",
            data={
                "client_id": self.config["CLIENT_ID"],
                "client_secret": self.config["CLIENT_SECRET"],
                "code": code,
                "redirect_uri": setup_state()["callback_url"],
            },
        )
        token = result.get("access_token")
        team = result.get("team")
        authed_user = result.get("authed_user")
        if (
            result.get("token_type") != "bot"
            or not isinstance(token, str)
            or not token
            or not isinstance(team, dict)
            or not isinstance(authed_user, dict)
        ):
            raise SlackUnavailable("Slack did not grant this app a bot token.")
        return result, token, team, authed_user

    def team_domain(self, token):
        result = self._request("GET", "/team.info", token)
        team = result.get("team", {})
        domain = team.get("domain")
        return domain if isinstance(domain, str) and re.fullmatch(r"[a-z0-9][a-z0-9-]{0,60}", domain) else ""

    def channels(self, token):
        collected = []
        cursor = ""
        for _ in range(5):
            data = {"types": "public_channel,private_channel", "limit": 200, "exclude_archived": "true"}
            if cursor:
                data["cursor"] = cursor
            result = self._request("GET", "/conversations.list", token, data=data)
            values = result.get("channels", [])
            if not isinstance(values, list):
                raise SlackUnavailable()
            collected.extend(channel for channel in values if isinstance(channel, dict))
            cursor = (result.get("response_metadata") or {}).get("next_cursor", "")
            if not cursor:
                return collected
        raise SlackUnavailable("Select at most 1000 Slack channels for this app.")

    def channel_history(self, token, channel_id):
        result = self._request("GET", "/conversations.history", token, data={"channel": channel_id, "limit": 100})
        messages = result.get("messages", [])
        if not isinstance(messages, list):
            raise SlackUnavailable()
        return messages

    def fetch_message(self, token, channel_id, ts):
        result = self._request(
            "GET", "/conversations.replies", token, data={"channel": channel_id, "ts": ts, "limit": 1}
        )
        messages = result.get("messages", [])
        if not isinstance(messages, list) or not messages or not isinstance(messages[0], dict):
            raise SlackUnavailable()
        return messages[0]

    def user_info(self, token, user_id):
        result = self._request("GET", "/users.info", token, data={"user": user_id})
        user = result.get("user")
        if not isinstance(user, dict):
            raise SlackUnavailable()
        return user

    def unfurl(self, token, channel, ts, unfurls):
        return self._request(
            "POST",
            "/chat.unfurl",
            token,
            data={"channel": channel, "ts": ts, "unfurls": json.dumps(unfurls)},
        )


def post_response_url(url, payload):
    """Deliver a slash-command result to Slack's response_url; never raise."""
    if not isinstance(url, str) or not url.startswith(RESPONSE_URL_PREFIX) or len(url) > 2000:
        return False
    try:
        response = requests.post(url, json=payload, timeout=(5, 10), allow_redirects=False)
        if not 200 <= response.status_code < 300:
            logger.warning("Slack response_url delivery failed with status %s", response.status_code)
        return 200 <= response.status_code < 300
    except requests.RequestException as error:
        logger.warning("Slack response_url delivery failed: %s", error)
        return False
