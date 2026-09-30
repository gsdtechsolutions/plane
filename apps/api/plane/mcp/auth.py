# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""API-key authentication for the MCP endpoint.

MCP clients send the Plane API key as ``Authorization: Bearer <key>``; the
classic ``X-API-Key`` header is accepted as a fallback so every client shape
works. Validation, throttling and audit behaviour are identical to the
external REST API.
"""

# Module imports
from plane.api.middleware.api_authentication import APIKeyAuthentication


class MCPAPIKeyAuthentication(APIKeyAuthentication):
    www_authenticate_realm = "mcp"
    auth_header_name = "Authorization"

    def get_api_token(self, request):
        header = request.headers.get("Authorization", "")
        if header.lower().startswith("bearer "):
            token = header[7:].strip()
            if token:
                return token
        # Fall back to the classic Plane API header
        return request.headers.get("X-Api-Key")

    def authenticate_header(self, request):
        # DRF only maps AuthenticationFailed to 401 when this is present;
        # MCP clients expect 401 (not 403) for bad credentials.
        return f'Bearer realm="{self.www_authenticate_realm}"'
