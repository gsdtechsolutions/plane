# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import re
import time
from urllib.parse import urlencode, urlsplit

import jwt
import requests
from django.conf import settings
from rest_framework.exceptions import APIException

GITHUB_COM = "github.com"


class GitHubUnavailable(APIException):
    status_code = 503
    default_detail = "GitHub could not complete this request. Check the connection and try again."


def normalize_host(value):
    """Return the canonical host key: "github.com" or a GitHub Enterprise origin.

    Accepts an optional scheme and trailing slash so pasted server addresses work;
    a hostname needs at least one dot (or be localhost) to be taken seriously.
    """
    if not isinstance(value, str):
        return None
    value = value.strip().lower()
    if value in ("", GITHUB_COM, "https://github.com"):
        return GITHUB_COM
    if "://" not in value:
        value = "https://" + value
    value = value.rstrip("/")
    if not re.fullmatch(r"https?://[a-z0-9.-]+(?::[0-9]{1,5})?", value):
        return None
    parsed = urlsplit(value)
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment or parsed.username or parsed.password:
        return None
    hostname = parsed.hostname or ""
    if parsed.scheme == "http" and hostname not in ("localhost", "127.0.0.1"):
        # Plain HTTP is only meaningful for a locally hosted Enterprise Server.
        return None
    if "." not in hostname and hostname not in ("localhost", "127.0.0.1"):
        return None
    return parsed.netloc


def web_base(host):
    return "https://github.com" if host == GITHUB_COM else f"https://{host}"


def api_base(host):
    return "https://api.github.com" if host == GITHUB_COM else f"https://{host}/api/v3"


def host_display(host):
    return "GitHub" if host == GITHUB_COM else host


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


def base_override():
    """Optional operator-pinned public origin (env GITHUB_APP_BASE_URL or settings)."""
    overrides = getattr(settings, "GITHUB_DELIVERY", {})
    origin = overrides.get("BASE_URL", "")
    return origin if valid_origin(origin) else ""


def board_origin(request):
    """Best-effort public origin of this board, validated before it is used in redirects."""
    override = base_override()
    if override:
        return override
    forwarded_host = request.headers.get("X-Forwarded-Host", "")
    host = forwarded_host.split(",")[0].strip() or request.get_host()
    forwarded_proto = request.headers.get("X-Forwarded-Proto", "")
    proto = forwarded_proto.split(",")[0].strip() or request.scheme
    host = host.rstrip(".")
    origin = f"{proto}://{host}"
    return origin if valid_origin(origin) else None


class GitHubClient:
    """Host-aware GitHub API client.

    `host` is "github.com" or a GitHub Enterprise Server origin. `app` is an
    optional GitHubApp model row; without it only manifest-flow calls work.
    """

    def __init__(self, host, app=None):
        self.host = normalize_host(host)
        if not self.host:
            raise GitHubUnavailable("Enter a valid GitHub address.")
        self.app = app

    def _request(self, method, path, token=None, *, data=None, oauth=False):
        base = web_base(self.host) if oauth else api_base(self.host)
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

    def _require_app(self):
        if self.app is None:
            raise GitHubUnavailable("This GitHub connection has no stored application. Reconnect the account.")
        return self.app

    def app_token(self):
        from .crypto import decrypt_secret

        app = self._require_app()
        try:
            return jwt.encode(
                {"iat": int(time.time()) - 60, "exp": int(time.time()) + 540, "iss": app.client_id},
                decrypt_secret(app.private_key).replace("\\n", "\n"),
                algorithm="RS256",
            )
        except (ValueError, jwt.PyJWTError) as error:
            raise GitHubUnavailable("GitHub App signing key is invalid.") from error

    def exchange_manifest_code(self, code):
        """Complete the GitHub App manifest flow: one-time code for App credentials."""
        if not isinstance(code, str) or not re.fullmatch(r"[A-Za-z0-9]{1,255}", code):
            raise GitHubUnavailable()
        result = self._request("POST", f"/app-manifests/{code}/conversions")
        app_id = result.get("id")
        slug = result.get("slug")
        client_id = result.get("client_id")
        client_secret = result.get("client_secret")
        pem = result.get("pem")
        webhook_secret = result.get("webhook_secret")
        if (
            isinstance(app_id, bool)
            or not isinstance(app_id, int)
            or not isinstance(slug, str)
            or not re.fullmatch(r"[A-Za-z0-9-]+", slug)
            or not isinstance(client_id, str)
            or not client_id
            or not isinstance(client_secret, str)
            or not client_secret
            or not isinstance(pem, str)
            or "PRIVATE KEY" not in pem
            or not isinstance(webhook_secret, str)
            or not webhook_secret
        ):
            raise GitHubUnavailable("GitHub did not return complete App credentials.")
        return {
            "app_id": app_id,
            "slug": slug,
            "client_id": client_id,
            "client_secret": client_secret,
            "private_key": pem,
            "webhook_secret": webhook_secret,
        }

    def installation_url(self, state):
        app = self._require_app()
        return f"{web_base(self.host)}/apps/{app.slug}/installations/new?state={state}"

    def authorization_url(self, state, redirect_uri):
        app = self._require_app()
        return (
            web_base(self.host)
            + "/login/oauth/authorize?"
            + urlencode({"client_id": app.client_id, "state": state, "redirect_uri": redirect_uri})
        )

    def exchange_code(self, code, redirect_uri):
        from .crypto import decrypt_secret

        app = self._require_app()
        result = self._request(
            "POST",
            "/login/oauth/access_token",
            oauth=True,
            data={
                "client_id": app.client_id,
                "client_secret": decrypt_secret(app.client_secret),
                "code": code,
                "redirect_uri": redirect_uri,
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

    def verify_installation(self, code, installation_id, redirect_uri):
        app = self._require_app()
        token = self.exchange_code(code, redirect_uri)
        accessible = self.paginated("/user/installations", token, "installations")
        if not any(item.get("id") == installation_id and item.get("app_id") == app.app_id for item in accessible):
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied("Your GitHub account cannot authorize this installation.")
        installation = self._request("GET", f"/app/installations/{installation_id}", self.app_token())
        if (
            installation.get("id") != installation_id
            or installation.get("app_id") != app.app_id
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
