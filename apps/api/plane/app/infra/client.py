# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Shared plumbing for outbound infra-service calls (Coolify, Grafana).

Every request goes through ``plane.utils.url_security.pinned_fetch``: the host
is resolved and pinned to a validated IP (no DNS rebinding), redirects are
never auto-followed, and ambient proxies are ignored.

Connection base URLs are operator-configured by workspace admins and routinely
point at self-hosted infrastructure on private subnets — that is the point of
the feature. The connection's own hostname is therefore passed as its own
``allowed_hosts`` entry (operator-trusted path, still IP-pinned), with one
hard exception: link-local / cloud-metadata addresses are rejected regardless.

Any transport, auth, or payload failure is normalized to ``InfraUnavailable``
so proxy endpoints return one predictable 503-style error shape.
"""

# Python imports
import ipaddress
from urllib.parse import urlsplit

# Django imports
from django.core.cache import cache

# Third party imports
import requests
from rest_framework.exceptions import APIException

# Module imports
from plane.utils.url_security import pinned_fetch

CACHE_TIMEOUT_SECONDS = 60


class InfraUnavailable(APIException):
    status_code = 503
    default_detail = "External infrastructure service is unreachable."
    default_code = "infra_unavailable"


class InfraConfigError(APIException):
    status_code = 400
    default_detail = "External infrastructure service URL is not usable."
    default_code = "infra_config_error"


def assert_host_allowed(url):
    """Reject connection hosts that must never be trusted, even by admins:
    link-local ranges carry cloud metadata endpoints (169.254.169.254 etc.).
    Raises InfraConfigError; cheap and DNS-free."""
    hostname = urlsplit(url).hostname
    if not hostname:
        raise InfraConfigError("Connection base URL has no hostname.")
    literal = hostname.strip("[]")
    try:
        ip = ipaddress.ip_address(literal)
    except ValueError:
        return  # a name, not an IP literal; pinned_fetch re-resolves safely
    if ip.is_link_local:
        raise InfraConfigError("Link-local/metadata addresses are not allowed for infra connections.")


def _trusted_hosts_for(base_url):
    hostname = urlsplit(base_url).hostname
    return [hostname] if hostname else []


def infra_request_json(method, url, api_token="", timeout=10, base_url=None):
    """One pinned, token-authed JSON request. Raises InfraUnavailable (503)
    or InfraConfigError (400) — never leaks the token in errors."""
    assert_host_allowed(url)
    headers = {"Accept": "application/json"}
    if api_token:
        headers["Authorization"] = f"Bearer {api_token}"
    try:
        response = pinned_fetch(
            method,
            url,
            allowed_hosts=_trusted_hosts_for(base_url or url),
            headers=headers,
            timeout=timeout,
        )
    except ValueError as exc:
        raise InfraUnavailable(f"Blocked or unresolvable URL for external service ({exc}).") from exc
    except requests.RequestException as exc:
        raise InfraUnavailable(f"Could not reach external service ({exc.__class__.__name__}).") from exc
    if response.status_code in (401, 403):
        raise InfraUnavailable("External service rejected the configured API token.")
    if response.status_code >= 400:
        raise InfraUnavailable(f"External service returned HTTP {response.status_code}.")
    try:
        return response.json()
    except ValueError as exc:
        raise InfraUnavailable("External service returned a non-JSON response.") from exc


def cached_json(cache_key, producer, refresh=False, timeout=CACHE_TIMEOUT_SECONDS):
    """Short-lived cache wrapper for proxied external reads. ``refresh=True``
    (the UI's manual refresh) bypasses and repopulates."""
    if not refresh:
        cached = cache.get(cache_key)
        if cached is not None:
            return cached
    value = producer()
    cache.set(cache_key, value, timeout)
    return value


def normalize_base_url(base_url):
    """Strip a trailing slash so ``f"{base_url}/api/v1/..."`` joins predictably."""
    return (base_url or "").rstrip("/")
