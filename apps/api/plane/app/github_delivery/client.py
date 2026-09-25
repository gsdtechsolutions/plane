# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import os
import re
import time
from urllib.parse import urlencode, urlsplit

import jwt
import requests
from django.conf import settings
from rest_framework.exceptions import APIException

CONFIG_KEYS = ("APP_ID", "SLUG", "PRIVATE_KEY", "CLIENT_ID", "CLIENT_SECRET", "WEBHOOK_SECRET", "BASE_URL")


class GitHubUnavailable(APIException):
    status_code = 503
    default_detail = "GitHub could not complete this request. Check the connection and try again."


def environment_key(key):
    return "GITHUB_APP_ID" if key == "APP_ID" else f"GITHUB_APP_{key}"


def configuration():
    overrides = getattr(settings, "GITHUB_DELIVERY", {})
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
    valid_app = str(config["APP_ID"]).isdigit() and re.fullmatch(r"[A-Za-z0-9-]+", config["SLUG"] or "")
    base = config["BASE_URL"].rstrip("/") if valid_url else ""
    return {
        "configured": not missing and bool(valid_url and valid_app),
        "missing_settings": missing,
        "configuration_error": "Use a valid public HTTPS board origin, numeric App ID and App slug."
        if not missing and not (valid_url and valid_app)
        else None,
        "setup_url": f"{base}/api/github-delivery/setup/" if base else None,
        "callback_url": f"{base}/api/github-delivery/callback/" if base else None,
        "webhook_url": f"{base}/api/github-delivery/webhooks/" if base else None,
        "permissions": ["Metadata: read", "Pull requests: read", "Contents: read"],
    }


def require_configuration():
    if not setup_state()["configured"]:
        raise GitHubUnavailable("GitHub App is not configured. Ask an instance administrator to complete setup.")
    return configuration()


class GitHubClient:
    def __init__(self):
        self.config = require_configuration()

    def _request(self, method, path, token=None, *, data=None, oauth=False):
        base = "https://github.com" if oauth else "https://api.github.com"
        if not path.startswith("/") or path.startswith("//"):
            raise GitHubUnavailable()
        headers = {
            "Accept": "application/json" if oauth else "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            response = requests.request(
                method, base + path, json=data, headers=headers, timeout=(5, 20), allow_redirects=False
            )
            if not 200 <= response.status_code < 300:
                raise GitHubUnavailable()
            result = response.json()
            if not isinstance(result, (dict, list)) or isinstance(result, dict) and result.get("error"):
                raise GitHubUnavailable()
            return result
        except (requests.RequestException, ValueError) as error:
            raise GitHubUnavailable() from error

    def app_token(self):
        try:
            return jwt.encode(
                {"iat": int(time.time()) - 60, "exp": int(time.time()) + 540, "iss": self.config["CLIENT_ID"]},
                self.config["PRIVATE_KEY"].replace("\\n", "\n"),
                algorithm="RS256",
            )
        except (ValueError, jwt.PyJWTError) as error:
            raise GitHubUnavailable("GitHub App signing key is invalid.") from error

    def installation_url(self, state):
        return f"https://github.com/apps/{self.config['SLUG']}/installations/new?{urlencode({'state': state})}"

    def authorization_url(self, state):
        return "https://github.com/login/oauth/authorize?" + urlencode(
            {
                "client_id": self.config["CLIENT_ID"],
                "state": state,
                "redirect_uri": setup_state()["callback_url"],
            }
        )

    def exchange_code(self, code):
        result = self._request(
            "POST",
            "/login/oauth/access_token",
            oauth=True,
            data={
                "client_id": self.config["CLIENT_ID"],
                "client_secret": self.config["CLIENT_SECRET"],
                "code": code,
                "redirect_uri": setup_state()["callback_url"],
            },
        )
        token = result.get("access_token")
        if not isinstance(token, str) or not token:
            raise GitHubUnavailable()
        return token

    def paginated(self, path, token, key):
        collected = []
        for page in range(1, 11):
            data = self._request("GET", f"{path}?per_page=100&page={page}", token)
            values = data.get(key, [])
            if not isinstance(values, list):
                raise GitHubUnavailable()
            collected.extend(values)
            if len(values) < 100 or len(collected) >= data.get("total_count", 10**9):
                return collected
        raise GitHubUnavailable("Select at most 1000 repositories for this GitHub App installation.")

    def verify_installation(self, code, installation_id):
        token = self.exchange_code(code)
        accessible = self.paginated("/user/installations", token, "installations")
        if not any(
            item.get("id") == installation_id and item.get("app_id") == int(self.config["APP_ID"])
            for item in accessible
        ):
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied("Your GitHub account cannot authorize this installation.")
        installation = self._request("GET", f"/app/installations/{installation_id}", self.app_token())
        if (
            installation.get("id") != installation_id
            or installation.get("app_id") != int(self.config["APP_ID"])
            or installation.get("suspended_at")
        ):
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied("This installation is not available to this GitHub App.")
        repositories = self.paginated(f"/user/installations/{installation_id}/repositories", token, "repositories")
        user = self._request("GET", "/user", token)
        return installation, repositories, user

    def installation_token(self, installation_id, repository_id=None):
        data = {"permissions": {"metadata": "read", "pull_requests": "read", "contents": "read"}}
        if repository_id:
            data["repository_ids"] = [repository_id]
        result = self._request(
            "POST", f"/app/installations/{installation_id}/access_tokens", self.app_token(), data=data
        )
        if not result.get("token"):
            raise GitHubUnavailable()
        return result["token"]

    def repositories(self, connection):
        token = self.installation_token(connection.installation_id)
        allowed = set(connection.authorized_repository_ids)
        return [
            repo
            for repo in self.paginated("/installation/repositories", token, "repositories")
            if repo.get("id") in allowed
        ]

    def pull_request(self, mapping, number):
        token = self.installation_token(mapping.connection.installation_id, mapping.repository_id)
        return self._request("GET", f"/repos/{mapping.full_name}/pulls/{number}", token)

    def recent_items(self, mapping):
        token = self.installation_token(mapping.connection.installation_id, mapping.repository_id)
        pulls = self._request("GET", f"/repos/{mapping.full_name}/pulls?state=all&per_page=100", token)
        releases = self._request("GET", f"/repos/{mapping.full_name}/releases?per_page=100", token)
        return pulls, releases
