# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Thin Asana REST API client (personal access token auth).

Only the endpoints the sync engine needs: workspaces, projects, sections,
tasks (+subtasks +stories), tags, users, webhooks. Pagination and bounded
retries mirror the conventions used by plane.bgtasks webhook delivery.
"""

# Python imports
import logging
import time
from typing import Any, Iterator, Optional

# Third party imports
import requests

logger = logging.getLogger(__name__)

ASANA_API_BASE = "https://app.asana.com/api/1.0"
DEFAULT_PAGE_SIZE = 100
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 2


class AsanaAPIError(Exception):
    """Asana API returned a non-recoverable error (4xx other than rate limit)."""

    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


class AsanaAuthError(AsanaAPIError):
    """401/403 — token invalid or revoked."""


def _unwrap(resp: dict) -> dict:
    """Single-resource responses arrive as {"data": {...}}; list endpoints are
    unwrapped by _paginate. Callers of the methods below consume task/webhook
    fields directly (task["gid"], webhook.get("gid"), story.get("gid")), so the
    envelope is stripped here. An empty-body {} passes through untouched."""
    if isinstance(resp, dict) and set(resp.keys()) == {"data"} and isinstance(resp["data"], dict):
        return resp["data"]
    return resp


def _request(token: str, method: str, path: str, params: Optional[dict] = None, json_body: Optional[dict] = None) -> dict:
    url = f"{ASANA_API_BASE}{path}"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    last_error: Optional[Exception] = None
    for attempt in range(MAX_RETRIES):
        try:
            response = requests.request(method, url, headers=headers, params=params, json=json_body, timeout=30)
        except requests.RequestException as exc:
            last_error = exc
            time.sleep(RETRY_BACKOFF_SECONDS * (attempt + 1))
            continue

        if response.status_code in (401, 403):
            raise AsanaAuthError("Asana rejected the credentials.", status_code=response.status_code)
        if response.status_code == 429 or response.status_code >= 500:
            last_error = AsanaAPIError(
                f"Asana returned {response.status_code}", status_code=response.status_code
            )
            time.sleep(RETRY_BACKOFF_SECONDS * (attempt + 1))
            continue
        if response.status_code >= 400:
            raise AsanaAPIError(
                f"Asana returned {response.status_code}: {response.text[:300]}", status_code=response.status_code
            )
        # 200 with empty body happens on some DELETEs.
        if not response.content:
            return {}
        return response.json()

    raise AsanaAPIError(f"Asana request failed after {MAX_RETRIES} attempts: {last_error}")


def _paginate(token: str, path: str, params: Optional[dict] = None) -> Iterator[dict]:
    """Yield items across Asana's offset-based pagination."""
    query = dict(params or {})
    query.setdefault("limit", DEFAULT_PAGE_SIZE)
    query.setdefault("opt_fields", "")
    while True:
        payload = _request(token, "GET", path, params=query)
        for item in payload.get("data", []):
            yield item
        next_page = payload.get("next_page")
        if not next_page or not next_page.get("offset"):
            return
        query["offset"] = next_page["offset"]


class AsanaClient:
    """Scoped wrapper around a single connection's PAT."""

    def __init__(self, token: str):
        self.token = token

    # --- identity & discovery -------------------------------------------------

    def me(self) -> dict:
        return _unwrap(_request(self.token, "GET", "/users/me"))

    def workspaces(self) -> list[dict]:
        return list(_paginate(self.token, "/workspaces"))

    def projects(self, workspace_gid: str) -> list[dict]:
        return list(
            _paginate(
                self.token,
                f"/workspaces/{workspace_gid}/projects",
                params={"opt_fields": "name,permalink_url,archived,modified_at"},
            )
        )

    def project(self, project_gid: str) -> dict:
        return _request(
            self.token, "GET", f"/projects/{project_gid}", params={"opt_fields": "name,permalink_url,modified_at"}
        )

    def sections(self, project_gid: str) -> list[dict]:
        return list(_paginate(self.token, f"/projects/{project_gid}/sections", params={"opt_fields": "name"}))

    def project_memberships(self, project_gid: str) -> list[dict]:
        return list(
            _paginate(
                self.token,
                f"/projects/{project_gid}/project_memberships",
                params={"opt_fields": "member.name,member.gid"},
            )
        )

    def tags(self, workspace_gid: str) -> list[dict]:
        return list(_paginate(self.token, f"/workspaces/{workspace_gid}/tags", params={"opt_fields": "name"}))

    # --- tasks & stories -------------------------------------------------------

    def tasks(self, project_gid: str, modified_since: Optional[str] = None) -> list[dict]:
        """Tasks of a project, newest-modified first. `modified_since` (RFC3339) filters server-side."""
        params: dict[str, Any] = {
            "opt_fields": (
                "name,notes,html_notes,completed,due_on,start_on,created_at,modified_at,completed_at,"
                "permalink_url,parent,num_subtasks,assignee,assignee.name,tags,tags.name,"
                "memberships.section,memberships.project"
            )
        }
        if modified_since:
            params["modified_since"] = modified_since
        return list(_paginate(self.token, f"/projects/{project_gid}/tasks", params=params))

    def task(self, task_gid: str) -> dict:
        return _unwrap(
            _request(
                self.token,
                "GET",
                f"/tasks/{task_gid}",
                params={
                    "opt_fields": (
                        "name,notes,html_notes,completed,due_on,start_on,created_at,modified_at,completed_at,"
                        "permalink_url,parent,num_subtasks,assignee,assignee.name,tags,tags.name,"
                        "memberships.section,memberships.project"
                    )
                },
            )
        )

    def subtasks(self, task_gid: str) -> list[dict]:
        return list(
            _paginate(
                self.token,
                f"/tasks/{task_gid}/subtasks",
                params={
                    "opt_fields": (
                        "name,notes,html_notes,completed,due_on,created_at,modified_at,completed_at,"
                        "permalink_url,parent,num_subtasks,assignee,assignee.name"
                    )
                },
            )
        )

    def stories(self, task_gid: str) -> list[dict]:
        return list(
            _paginate(
                self.token,
                f"/tasks/{task_gid}/stories",
                params={"opt_fields": "gid,created_at,created_by,created_by.name,type,text,html_text,resource_subtype"},
            )
        )

    # --- mutations (push direction) ---------------------------------------------

    def create_task(self, workspace_gid: str, data: dict) -> dict:
        payload = {"data": data}
        return _unwrap(_request(self.token, "POST", f"/workspaces/{workspace_gid}/tasks", json_body=payload))

    def update_task(self, task_gid: str, data: dict) -> dict:
        payload = {"data": data}
        return _unwrap(_request(self.token, "PUT", f"/tasks/{task_gid}", json_body=payload))

    def add_task_to_project(self, task_gid: str, project_gid: str, section_gid: Optional[str] = None) -> None:
        payload = {"data": {"project": project_gid}}
        if section_gid:
            payload["data"]["section"] = section_gid  # type: ignore[index]
        _request(self.token, "POST", f"/tasks/{task_gid}/addProject", json_body=payload)

    def move_task_to_section(self, task_gid: str, section_gid: str) -> None:
        payload = {"data": {"section": section_gid}}
        _request(self.token, "POST", f"/sections/{section_gid}/insertTask", json_body=payload)

    def add_tag(self, task_gid: str, tag_gid: str) -> None:
        _request(self.token, "POST", f"/tasks/{task_gid}/addTag", json_body={"data": {"tag": tag_gid}})

    def create_story(self, task_gid: str, text: str) -> dict:
        payload = {"data": {"text": text}}
        return _unwrap(_request(self.token, "POST", f"/tasks/{task_gid}/stories", json_body=payload))

    def update_story(self, story_gid: str, text: str) -> dict:
        payload = {"data": {"text": text}}
        return _unwrap(_request(self.token, "PUT", f"/stories/{story_gid}", json_body=payload))

    # --- webhooks ----------------------------------------------------------------

    def create_webhook(self, resource_gid: str, target_url: str) -> dict:
        payload = {"data": {"resource": resource_gid, "target": target_url, "active": True}}
        return _unwrap(_request(self.token, "POST", "/webhooks", json_body=payload))

    def delete_webhook(self, webhook_gid: str) -> None:
        _request(self.token, "DELETE", f"/webhooks/{webhook_gid}")
