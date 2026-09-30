# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""The MCP HTTP endpoint.

A stateless MCP streamable-HTTP server implemented on the DRF request
cycle, so Plane API-key authentication, rate limiting and the token audit
log all apply unchanged. Every POST carries JSON-RPC and gets back
application/json (202 for notifications-only batches).
"""

# Python imports
import json
import logging

# Third party imports
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

# Module imports
from plane.api.views.base import BaseAPIView
from plane.mcp import protocol, tools
from plane.mcp.auth import MCPAPIKeyAuthentication
from plane.mcp.resolvers import McpToolError

logger = logging.getLogger("plane.api.mcp")

# Maximum JSON-RPC payload we accept (protects the parser)
MAX_BODY_BYTES = 1_000_000


class MCPEndpoint(BaseAPIView):
    authentication_classes = [MCPAPIKeyAuthentication]
    permission_classes = [IsAuthenticated]
    http_method_names = ["post", "get", "options"]

    def perform_content_negotiation(self, request, force=False):
        """Always respond application/json regardless of the Accept header.

        MCP clients probe GET /mcp with `Accept: text/event-stream`; DRF's
        default negotiation would answer 406, which the TypeScript SDK
        treats as a fatal transport error (it expects 405 = "no SSE").
        """
        from rest_framework.renderers import JSONRenderer

        return JSONRenderer(), JSONRenderer.media_type

    # ------------------------------------------------------------ dispatch

    def get(self, request):
        """No server-initiated streams: the transport is POST-only."""
        return self._jsonrpc_response(
            protocol.jsonrpc_error(None, protocol.INVALID_REQUEST, "MCP endpoint accepts POST requests only."),
            status_code=status.HTTP_405_METHOD_NOT_ALLOWED,
        )

    def post(self, request):
        if request.body and len(request.body) > MAX_BODY_BYTES:
            return self._jsonrpc_response(
                protocol.jsonrpc_error(None, protocol.INVALID_REQUEST, "Payload too large."),
                status_code=status.HTTP_413_REQUEST_ENTITY,
            )

        try:
            payload = json.loads(request.body.decode("utf-8")) if request.body else None
        except (ValueError, UnicodeDecodeError):
            # Transport-level failure: cannot even parse, so 400
            return self._jsonrpc_response(
                protocol.jsonrpc_error(None, protocol.PARSE_ERROR, "Invalid JSON payload."),
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        # A JSON-RPC batch (kept for 2025-03-26-era clients)
        if isinstance(payload, list):
            if not payload:
                return self._jsonrpc_response(
                    protocol.jsonrpc_error(None, protocol.INVALID_REQUEST, "Empty batch."),
                    status_code=status.HTTP_400_BAD_REQUEST,
                )
            responses = [response for response in (self._handle_message(message) for message in payload) if response]
            if not responses:
                return Response(status=status.HTTP_202_ACCEPTED)
            return self._jsonrpc_response(responses)

        if not isinstance(payload, dict):
            return self._jsonrpc_response(
                protocol.jsonrpc_error(None, protocol.INVALID_REQUEST, "Request must be a JSON-RPC message."),
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        response = self._handle_message(payload)
        if response is None:
            return Response(status=status.HTTP_202_ACCEPTED)
        return self._jsonrpc_response(response)

    # ------------------------------------------------------------- helpers

    def _jsonrpc_response(self, body, status_code=status.HTTP_200_OK):
        return Response(body, status=status_code, content_type="application/json")

    def _handle_message(self, message):
        """Handle one JSON-RPC message; returns a response dict or None for
        notifications."""
        if not isinstance(message, dict):
            return protocol.jsonrpc_error(None, protocol.INVALID_REQUEST, "Invalid JSON-RPC message.")

        method = message.get("method")
        message_id = message.get("id")
        is_request = "id" in message

        if not method or not isinstance(method, str):
            return protocol.jsonrpc_error(message_id, protocol.INVALID_REQUEST, "Missing 'method'.")

        params = message.get("params") or {}
        if not isinstance(params, dict):
            return protocol.jsonrpc_error(message_id, protocol.INVALID_PARAMS, "'params' must be an object.")

        try:
            if method == "initialize":
                return protocol.jsonrpc_result(message_id, self._initialize(params))
            if method.startswith("notifications/"):
                return None
            if method == "ping":
                return protocol.jsonrpc_result(message_id, {})
            if method == "tools/list":
                return protocol.jsonrpc_result(message_id, {"tools": tools.TOOL_DEFINITIONS})
            if method == "tools/call":
                return protocol.jsonrpc_result(message_id, self._call_tool(self.request, params))
            if method == "resources/list":
                return protocol.jsonrpc_result(message_id, {"resources": []})
            if method == "resources/templates/list":
                return protocol.jsonrpc_result(message_id, {"resourceTemplates": []})
            if method == "prompts/list":
                return protocol.jsonrpc_result(message_id, {"prompts": []})
            if method == "logging/setLevel":
                return protocol.jsonrpc_result(message_id, {})
            if is_request:
                return protocol.jsonrpc_error(
                    message_id, protocol.METHOD_NOT_FOUND, f"Method not found: {method}"
                )
            # Unknown notification: accepted and ignored
            return None
        except McpToolError as error:
            # Never expected outside tools/call; keep the loop alive anyway
            logger.exception("MCP unexpected tool error for %s", method)
            return protocol.jsonrpc_error(message_id, protocol.INTERNAL_ERROR, error.message)
        except Exception:
            logger.exception("MCP handler crashed for %s", method)
            return protocol.jsonrpc_error(message_id, protocol.INTERNAL_ERROR, "Internal server error.")

    # ------------------------------------------------------------ protocol

    def _initialize(self, params):
        return {
            "protocolVersion": protocol.negotiate_protocol_version(params.get("protocolVersion")),
            "capabilities": {
                "tools": {"listChanged": False},
                "resources": {},
                "prompts": {},
                "logging": {},
            },
            "serverInfo": {
                "name": protocol.SERVER_NAME,
                "version": protocol.SERVER_VERSION,
            },
            "instructions": protocol.SERVER_INSTRUCTIONS,
        }

    def _call_tool(self, request, params):
        name = params.get("name")
        if not name or not isinstance(name, str):
            raise McpToolError("Missing tool name.")

        try:
            result_text = tools.call_tool(request, name, params.get("arguments") or {})
        except KeyError:
            return protocol.tool_error_result(
                f"Unknown tool '{name}'. Use tools/list to see available tools."
            )
        except McpToolError as error:
            return protocol.tool_error_result(error.message)
        except Exception:
            logger.exception("MCP tool '%s' failed", name)
            return protocol.tool_error_result(f"Tool '{name}' failed with an internal error.")

        return protocol.tool_text_result(result_text)
