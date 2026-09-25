# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only

import base64
import hashlib
import hmac
import os
import re
import time
from urllib.parse import urlencode, urlsplit

import requests
from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from rest_framework.exceptions import APIException

CONFIG_KEYS = ("CLIENT_ID", "CLIENT_SECRET", "SIGNING_SECRET", "BASE_URL")
BOT_SCOPES = "channels:read,groups:read,channels:history,groups:history"
EVENT_SUBSCRIPTIONS = "message.channels, message.groups, channel_rename, app_uninstalled, tokens_revoked"
SIGNATURE_MAX_SKEW = 300


class SlackUnavailable(APIException):
    status_code = 503
    default_detail = "Slack could not complete this request. Check the connection and try again."


def environment_key(key):
    return "SLACK_APP_BASE_URL" if key == "BASE_URL" else f"SLACK_{key}"


def configuration():
    overrides = getattr(settings, "SLACK_DELIVERY", {})
    return {key: overrides.get(key, os.environ.get(environment_key(key), "")) for key in CONFIG_KEYS}


def setup_state():
    config = configuration()
    missing = [environment_key(key) for key, value in config.items() if not value]
    parsed = urlsplit(config["BASE_URL"])
    valid_url = parsed.scheme == "https" or (parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1"))
    valid_url = (
        valid_url
        and bool(parsed.netloc)
        and not parsed.username
        and not parsed.password
        and not parsed.query
        and not parsed.fragment
        and parsed.path in ("", "/")
    )
    valid_app = re.fullmatch(r"[0-9]{6,20}\.[0-9]{6,20}", config["CLIENT_ID"] or "") is not None
    base = config["BASE_URL"].rstrip("/") if valid_url else ""
    return {
        "configured": not missing and bool(valid_url and valid_app),
        "missing_settings": missing,
        "configuration_error": "Use a valid public HTTPS board origin and a Slack app client ID."
        if not missing and not (valid_url and valid_app)
        else None,
        "callback_url": f"{base}/api/slack-delivery/callback/" if base else None,
        "events_url": f"{base}/api/slack-delivery/webhooks/" if base else None,
        "scopes": BOT_SCOPES.split(","),
        "event_subscriptions": EVENT_SUBSCRIPTIONS.split(", "),
        "permissions": ["Channels: read history and info", "Groups: read history and info"],
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
                raise SlackUnavailable()
            result = response.json()
            if not isinstance(result, dict) or result.get("ok") is not True:
                raise SlackUnavailable()
            return result
        except (requests.RequestException, ValueError) as error:
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
