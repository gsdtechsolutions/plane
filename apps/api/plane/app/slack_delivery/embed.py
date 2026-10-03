# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only

"""Short-lived, cookie-free issue previews for Slack's sandboxed iframe."""

import hashlib
import hmac
import re
import time
from urllib.parse import urlencode
from uuid import UUID

from django.conf import settings
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.utils.decorators import method_decorator
from django.views.decorators.clickjacking import xframe_options_exempt
from rest_framework.permissions import AllowAny

from plane.app.views.base import BaseAPIView
from plane.db.models import Issue, IssueComment

EMBED_PATH = "/api/slack-delivery/embed/"
EMBED_TTL = 600
EMBED_CSP = "frame-ancestors https://*.slack.com https://*.slack-gov.com https://*.slack-mcps.com"


def _signature(query):
    key = hashlib.pbkdf2_hmac("sha256", settings.SECRET_KEY.encode(), b"plane.slack-embed.v1", 480_000)
    return hmac.new(key, query.encode(), hashlib.sha256).hexdigest()


def signed_embed_url(issue, base_url):
    params = {
        "workspace": str(issue.workspace_id),
        "issue": str(issue.id),
        "exp": int(time.time()) + EMBED_TTL,
    }
    query = urlencode(sorted(params.items()))
    return f"{base_url.rstrip('/')}{EMBED_PATH}?{query}&sig={_signature(query)}"


@method_decorator(xframe_options_exempt, name="dispatch")
class EmbedIssueEndpoint(BaseAPIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Content-Security-Policy"] = EMBED_CSP
        # Slack's docs require preview URLs to allow the app.slack.com origin.
        response["Access-Control-Allow-Origin"] = "https://app.slack.com"
        response["Cache-Control"] = "no-store"
        return response

    def get(self, request):
        params = request.query_params
        if set(params) != {"workspace", "issue", "exp", "sig"} or any(
            len(params.getlist(key)) != 1 for key in params
        ):
            raise Http404
        if not re.fullmatch(r"[0-9]{1,20}", params["exp"]) or not re.fullmatch(r"[0-9a-f]{64}", params["sig"]):
            raise Http404
        expires = int(params["exp"])
        now = int(time.time())
        if not now < expires <= now + EMBED_TTL:
            raise Http404
        query = urlencode(sorted((key, params[key]) for key in ("workspace", "issue", "exp")))
        if request.META.get("QUERY_STRING") != f"{query}&sig={params['sig']}" or not hmac.compare_digest(
            _signature(query), params["sig"]
        ):
            raise Http404
        try:
            workspace_id, issue_id = UUID(params["workspace"]), UUID(params["issue"])
        except ValueError:
            raise Http404
        issue = get_object_or_404(
            Issue.objects.select_related("project", "state"), id=issue_id, workspace_id=workspace_id
        )
        comments = (
            IssueComment.objects.filter(issue=issue, workspace_id=workspace_id)
            .select_related("actor")
            .order_by("-created_at", "-id")[:20]
        )
        return render(request, "slack_delivery/embed.html", {"issue": issue, "comments": comments})
