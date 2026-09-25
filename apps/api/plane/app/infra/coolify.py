# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Coolify API v1 adapter (read-only).

Endpoints used (Coolify v4 API, Bearer token auth):
    GET /api/v1/healthcheck            → connectivity + version probe
    GET /api/v1/applications           → picker list
    GET /api/v1/applications/{uuid}    → app detail (status, health-check config)
    GET /api/v1/deployments?uuid={id}  → deployment history

Responses vary across Coolify versions, so every field is extracted
defensively: a missing field becomes ``None`` rather than a 500. All network
I/O goes through :func:`plane.app.infra.client.infra_request_json`.
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
    """Cheap authenticated probe. Returns {"ok", "version"} or {"ok": False, "error"}."""
    try:
        data = _get(connection, "/api/v1/healthcheck", timeout=8)
    except Exception as exc:
        return {"ok": False, "error": str(getattr(exc, "detail", exc))}
    version = data.get("version") if isinstance(data, dict) else None
    return {"ok": True, "version": version, "service": "coolify"}


def list_applications(connection):
    """Normalized picker list: [{id, name, fqdn, description, status}]."""
    data = _get(connection, "/api/v1/applications", timeout=15)
    if not isinstance(data, list):
        data = data.get("data", []) if isinstance(data, dict) else []
    apps = []
    for item in data:
        if not isinstance(item, dict):
            continue
        apps.append(
            {
                "id": item.get("uuid") or str(item.get("id") or ""),
                "name": item.get("name") or "",
                "fqdn": item.get("fqdn") or "",
                "description": item.get("description") or "",
                "status": item.get("status") or None,
            }
        )
    return apps


def _first_fqdn(app):
    fqdn = app.get("fqdn") or ""
    if isinstance(fqdn, list):  # some versions return a list of domains
        fqdn = fqdn[0] if fqdn else ""
    return str(fqdn).split(",")[0].strip() if fqdn else ""


def normalize_deployment(item):
    if not isinstance(item, dict):
        return {}
    return {
        "id": str(item.get("deployment_uuid") or item.get("uuid") or item.get("id") or ""),
        "status": item.get("status") or None,
        "commit_sha": (item.get("commit") or None),
        "message": item.get("commit_message") or None,
        "created_at": item.get("created_at") or None,
        "finished_at": item.get("finished_at") or item.get("updated_at") or None,
    }


def normalize_app_status(app, deployments, base_url=""):
    """Merge app detail + deployment history into the UI status shape.

    Version resolution order: image tag (docker-based apps) → commit sha of the
    latest finished deployment (git-based apps). Fields the host's Coolify
    version does not expose stay None.
    """
    app = app if isinstance(app, dict) else {}
    deployments = [normalize_deployment(d) for d in (deployments or []) if isinstance(d, dict)]
    deployments = [d for d in deployments if d.get("id")][:5]

    latest = next(
        (d for d in deployments if (d.get("status") or "").lower() in ("finished", "success", "successful")),
        deployments[0] if deployments else None,
    )
    image_name = app.get("docker_registry_image_name") or app.get("image") or ""
    image_tag = str(image_name).rsplit(":", 1)[-1] if ":" in str(image_name) else None
    commit_sha = (latest or {}).get("commit_sha") or app.get("git_commit_sha") or None
    version_label = image_tag or (commit_sha[:7] if commit_sha else None)

    app_url = _first_fqdn(app)
    deep_link = None
    env = app.get("environment") or {}
    if isinstance(env, dict) and env.get("project_id") and env.get("name") and app.get("uuid") and base_url:
        deep_link = (
            f"{normalize_base_url(base_url)}/project/{env['project_id']}/{env['name']}/application/{app['uuid']}"
        )

    return {
        "status": app.get("status") or None,
        "health": {
            "enabled": bool(app.get("health_check_enabled")),
            "path": app.get("health_check_path") or None,
            "port": app.get("health_check_port") or None,
            "check_period": app.get("health_check_period") or None,
        },
        "version": {
            "label": version_label,
            "image_tag": image_tag,
            "commit_sha": commit_sha,
            "message": (latest or {}).get("message") or None,
            "deployed_at": (latest or {}).get("finished_at") or None,
        },
        "app_url": app_url or None,
        "deep_link": deep_link,
        "deployments": deployments,
    }


def get_application(connection, app_uuid):
    return _get(connection, f"/api/v1/applications/{app_uuid}")


def list_deployments(connection, app_uuid):
    data = _get(connection, f"/api/v1/deployments?uuid={app_uuid}", timeout=15)
    return data if isinstance(data, list) else data.get("data", []) if isinstance(data, dict) else []


def app_status(connection, app_uuid):
    """Detail + deployments, both defensive, merged via normalize_app_status."""
    app = get_application(connection, app_uuid)
    try:
        deployments = list_deployments(connection, app_uuid)
    except Exception:
        deployments = []  # app detail alone is still useful if history fails
    app["uuid"] = app.get("uuid") or app_uuid
    return normalize_app_status(app, deployments, base_url=connection.base_url)
