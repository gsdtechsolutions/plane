# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""HTTP client for the AFFiNE public API (cloud or self-hosted).

Endpoints follow the documented public REST surface:
  GET  /api/workspaces/{ws}                    workspace info
  GET  /api/workspaces/{ws}/docs               doc list (metadata incl. updatedAt)
  POST /api/workspaces/{ws}/docs               create doc (markdown payload)
  GET  /api/workspaces/{ws}/docs/{doc}         doc metadata
  GET  /api/workspaces/{ws}/docs/{doc}/markdown   markdown export
  PUT  /api/workspaces/{ws}/docs/{doc}         update doc (markdown payload)

Auth: Bearer token generated in AFFiNE account settings. This adapter is
response-shape tolerant (list or {data:...}, snake/camel keys) so it degrades
gracefully across AFFiNE server versions, and every failure raises
AffineError with a message safe to surface in the settings UI.
"""

import logging
from typing import Any, Optional
from urllib.parse import quote, urlparse

import requests

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30
MAX_TOKEN_LENGTH = 4096


class AffineError(Exception):
    """Raised for any AFFiNE API failure; message is UI-safe."""

    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


class AffineAuthError(AffineError):
    """Raised on 401/403 — the UI should prompt for a new token."""


def normalize_instance_url(url: str) -> str:
    """Validate and normalize an AFFiNE instance base URL.

    Returns the URL with scheme + host (no trailing slash). Rejects
    credentialed URLs (user:pass@host) and non-http(s) schemes — mirrors the
    fork's changelog-widget URL validation posture.
    """
    raw = (url or "").strip()
    if not raw:
        raise AffineError("AFFiNE instance URL is required.")
    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw
    try:
        parsed = urlparse(raw)
    except ValueError as exc:
        raise AffineError("Invalid AFFiNE instance URL.") from exc
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise AffineError("AFFiNE instance URL must be an http(s) URL with a host.")
    if parsed.username or parsed.password:
        raise AffineError("AFFiNE instance URL must not embed credentials.")
    # http is allowed so self-hosted LAN instances (http://affine.lan) work;
    # cloud (app.affine.pro) is always reached over https by the user's input.
    return f"{parsed.scheme}://{parsed.netloc}"


def validate_api_token(token: str) -> str:
    token = (token or "").strip()
    if not token:
        raise AffineError("AFFiNE API token is required.")
    if len(token) > MAX_TOKEN_LENGTH:
        raise AffineError("AFFiNE API token is too long.")
    return token


class AffineClient:
    """Thin requests wrapper over the AFFiNE public API."""

    def __init__(self, instance_url: str, api_token: str, timeout: int = DEFAULT_TIMEOUT):
        self.base_url = normalize_instance_url(instance_url)
        self.api_token = validate_api_token(api_token)
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {self.api_token}",
                "Accept": "application/json",
                "User-Agent": "Plane-AFFiNE-Sync/1.0",
            }
        )

    # ------------------------------------------------------------------ #
    # internals
    # ------------------------------------------------------------------ #

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        url = f"{self.base_url}/api/{path.lstrip('/')}"
        try:
            response = self.session.request(method, url, timeout=self.timeout, **kwargs)
        except requests.exceptions.SSLError as exc:
            raise AffineError(f"TLS certificate error talking to {self.base_url}: {exc}") from exc
        except requests.exceptions.ConnectionError as exc:
            raise AffineError(f"Could not reach AFFiNE at {self.base_url}.") from exc
        except requests.exceptions.Timeout as exc:
            raise AffineError(f"AFFiNE at {self.base_url} did not respond in {self.timeout}s.") from exc
        except requests.exceptions.RequestException as exc:
            raise AffineError(f"AFFiNE request failed: {exc}") from exc
        if response.status_code in (401, 403):
            raise AffineAuthError("AFFiNE rejected the API token (or it lacks access to this workspace).", response.status_code)
        if response.status_code == 429:
            raise AffineError("AFFiNE rate limit hit; retry shortly.", 429)
        if response.status_code >= 400:
            detail = self._error_detail(response)
            raise AffineError(detail, response.status_code)
        return response

    @staticmethod
    def _error_detail(response: requests.Response) -> str:
        try:
            payload = response.json()
        except ValueError:
            return f"AFFiNE returned HTTP {response.status_code}."
        if isinstance(payload, dict):
            for key in ("message", "error", "detail", "msg"):
                value = payload.get(key)
                if isinstance(value, str) and value:
                    return f"AFFiNE error: {value}"
        return f"AFFiNE returned HTTP {response.status_code}."

    @staticmethod
    def _payload(response: requests.Response) -> Any:
        """Accepts a bare list, a dict, or {data: ...} envelopes."""
        try:
            payload = response.json()
        except ValueError as exc:
            raise AffineError("AFFiNE returned a non-JSON response.") from exc
        if isinstance(payload, dict) and "data" in payload and not {"id", "guid"} & payload.keys():
            inner = payload["data"]
            return inner
        return payload

    @staticmethod
    def _get_any(entry: dict, *names: str, default=None):
        """Fetch the first present key among snake_case/camelCase variants."""
        for name in names:
            if name in entry and entry[name] is not None:
                return entry[name]
        return default

    @staticmethod
    def _doc_id(entry: Any) -> str:
        if not isinstance(entry, dict):
            raise AffineError("AFFiNE returned an unexpected doc entry.")
        doc_id = AffineClient._get_any(entry, "guid", "id", "docId", "doc_id")
        if not doc_id:
            raise AffineError("AFFiNE doc entry is missing an id.")
        return str(doc_id)

    # ------------------------------------------------------------------ #
    # API surface used by the sync engine
    # ------------------------------------------------------------------ #

    def test_connection(self) -> dict:
        """Probe the token; returns {id, name} of the authenticated identity or workspace list owner."""
        response = self._request("GET", "workspaces/")
        payload = self._payload(response)
        if isinstance(payload, dict) and payload:
            return {"id": str(self._get_any(payload, "id", "guid", default="")), "name": str(self._get_any(payload, "name", default=""))}
        return {"id": "", "name": ""}

    def list_workspaces(self) -> list:
        """List workspaces the token can see: [{id, name, member_count?}]."""
        response = self._request("GET", "workspaces/")
        payload = self._payload(response)
        entries = payload if isinstance(payload, list) else []
        if isinstance(payload, dict):
            # Some servers return {workspaces: [...]} or a single workspace dict.
            entries = self._get_any(payload, "workspaces", default=[])
            if isinstance(entries, dict):
                entries = [entries]
        workspaces = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            ws_id = self._get_any(entry, "id", "guid")
            if not ws_id:
                continue
            workspaces.append(
                {
                    "id": str(ws_id),
                    "name": str(self._get_any(entry, "name", default="Untitled workspace")),
                }
            )
        return workspaces

    def list_docs(self, workspace_id: str) -> list:
        """List docs in a workspace: [{guid, title, updated_at, created_at, trash?}]."""
        safe_id = quote(str(workspace_id), safe="")
        response = self._request("GET", f"workspaces/{safe_id}/docs/")
        payload = self._payload(response)
        entries = payload if isinstance(payload, list) else []
        if isinstance(payload, dict):
            entries = self._get_any(payload, "docs", default=[])
            if isinstance(entries, dict):
                entries = [entries]
        docs = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            doc_id = self._doc_id(entry)
            docs.append(
                {
                    "guid": doc_id,
                    "title": str(self._get_any(entry, "title", "name", default="Untitled")),
                    "updated_at": self._get_any(entry, "updatedAt", "updated_at", default=None),
                    "created_at": self._get_any(entry, "createdAt", "created_at", default=None),
                    "trash": bool(self._get_any(entry, "trash", "trashed", default=False)),
                }
            )
        return docs

    def get_doc_markdown(self, workspace_id: str, doc_id: str) -> str:
        """Export a doc as markdown. Falls back to body text on JSON envelopes."""
        safe_ws = quote(str(workspace_id), safe="")
        safe_doc = quote(str(doc_id), safe="")
        response = self._request("GET", f"workspaces/{safe_ws}/docs/{safe_doc}/markdown", headers={"Accept": "text/markdown, application/json"})
        content_type = response.headers.get("Content-Type", "")
        if "application/json" in content_type:
            payload = self._payload(response)
            if isinstance(payload, dict):
                markdown = self._get_any(payload, "markdown", "content", "body", default=None)
                if markdown is None:
                    raise AffineError("AFFiNE doc export returned no markdown content.")
                return str(markdown)
            if isinstance(payload, str):
                return payload
        return response.text

    def create_doc(self, workspace_id: str, title: str, markdown: str) -> str:
        """Create a doc; returns its guid."""
        safe_ws = quote(str(workspace_id), safe="")
        response = self._request(
            "POST",
            f"workspaces/{safe_ws}/docs/",
            json={"title": title, "markdown": markdown},
        )
        payload = self._payload(response)
        if isinstance(payload, dict):
            return self._doc_id(payload)
        raise AffineError("AFFiNE did not return the created doc id.")

    def update_doc(self, workspace_id: str, doc_id: str, title: str, markdown: str) -> None:
        """Replace a doc's content (markdown import semantics)."""
        safe_ws = quote(str(workspace_id), safe="")
        safe_doc = quote(str(doc_id), safe="")
        self._request(
            "PUT",
            f"workspaces/{safe_ws}/docs/{safe_doc}",
            json={"title": title, "markdown": markdown},
        )

    def delete_doc(self, workspace_id: str, doc_id: str) -> None:
        """Trash a doc (soft delete on the AFFiNE side)."""
        safe_ws = quote(str(workspace_id), safe="")
        safe_doc = quote(str(doc_id), safe="")
        self._request("DELETE", f"workspaces/{safe_ws}/docs/{safe_doc}")

    def close(self):
        self.session.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
