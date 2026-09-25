# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Grafana HTTP API adapter (read-only).

Endpoints used (Bearer token auth — a service-account token):
    GET /api/user    → auth probe (works for service accounts)
    GET /api/health  → unauthenticated version probe
    GET /api/search?type=dash-db&limit=100 → dashboard picker list

All network I/O goes through :func:`plane.app.infra.client.infra_request_json`.
"""

# Module imports
from plane.app.infra.client import infra_request_json, normalize_base_url


def _get(connection, path, timeout=10):
    return infra_request_json(
        "GET",
        f"{normalize_base_url(connection.base_url)}{path}",
        api_token=connection.get_api_token(),
        base_url=connection.base_url,
        timeout=timeout,
    )


def verify(connection):
    """Auth probe + version. Returns {"ok", "version"} or {"ok": False, "error"}."""
    result = {"ok": False, "version": None, "service": "grafana"}
    try:
        _get(connection, "/api/user", timeout=8)
    except Exception as exc:
        result["error"] = str(getattr(exc, "detail", exc))
        return result
    result["ok"] = True
    try:
        health = _get(connection, "/api/health", timeout=8)
        if isinstance(health, dict):
            result["version"] = health.get("version")
    except Exception:
        pass  # auth already proven; version display is best-effort
    return result


def list_dashboards(connection):
    """Normalized picker list: [{id, title, url, folder_title, tags}]."""
    data = _get(connection, "/api/search?type=dash-db&limit=100", timeout=15)
    if not isinstance(data, list):
        return []
    dashboards = []
    for item in data:
        if not isinstance(item, dict) or not item.get("uid"):
            continue
        dashboards.append(
            {
                "id": item.get("uid"),
                "title": item.get("title") or item.get("uid"),
                "url": item.get("url") or f"/d/{item.get('uid')}",
                "folder_title": item.get("folderTitle") or "",
                "tags": item.get("tags") or [],
            }
        )
    return dashboards


def dashboard_absolute_url(connection, dashboard):
    """Absolute deep link for a dashboard (discovered or manual uid)."""
    url = dashboard.get("url") or f"/d/{dashboard.get('id', '')}"
    base = normalize_base_url(connection.base_url)
    return f"{base}{url}" if url.startswith("/") else url
