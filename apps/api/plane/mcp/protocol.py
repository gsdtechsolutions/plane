# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Minimal JSON-RPC 2.0 / MCP protocol helpers.

The endpoint implements the stateless subset of the MCP streamable HTTP
transport: every POST carries JSON-RPC messages and gets back an
``application/json`` response (or 202 for notifications). No session id is
issued, which every current MCP client supports.
"""

# Python imports
import logging

logger = logging.getLogger("plane.api.mcp")

# MCP protocol versions this server can speak. When a client asks for a
# version we do not know we answer with the newest one we support.
LATEST_PROTOCOL_VERSION = "2025-11-25"
SUPPORTED_PROTOCOL_VERSIONS = [
    "2025-11-25",
    "2025-06-18",
    "2025-03-26",
    "2024-11-05",
]

# JSON-RPC error codes
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

SERVER_NAME = "plane-mcp"
SERVER_VERSION = "1.0.0"

SERVER_INSTRUCTIONS = (
    "This is the Plane work-management MCP server. It exposes the workspace tied to "
    "your Plane API key.\n\n"
    "CONVENTIONS\n"
    "- Work items are referenced by human refs like 'PROJ-14' (project identifier + sequence), "
    "not raw UUIDs. Project parameters accept the identifier (e.g. 'PROJ'), the name, or a UUID.\n"
    "- Assignees are workspace user emails (or 'me'). States are matched by name, "
    "case-insensitive; valid priorities are urgent, high, medium, low, none.\n"
    "- Every tool takes an optional workspace_slug; when omitted, the token's default "
    "workspace is used.\n"
    "- Descriptions accept plain text (description_text) or HTML (description_html); "
    "use description_text unless you already have HTML.\n"
    "- Start with plane_list_projects, then plane_list_states for a project before "
    "changing states.\n\n"
    "WRITE TOOLS create/update real work items visible to the whole team - confirm "
    "intent before bulk writes."
)


def jsonrpc_result(message_id, result):
    return {"jsonrpc": "2.0", "id": message_id, "result": result}


def jsonrpc_error(message_id, code, message, data=None):
    error = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": message_id, "error": error}


def tool_text_result(text):
    return {"content": [{"type": "text", "text": text}]}


def tool_error_result(text):
    return {"content": [{"type": "text", "text": text}], "isError": True}


def negotiate_protocol_version(client_version):
    """Echo the client's version when supported, else our latest."""
    if client_version in SUPPORTED_PROTOCOL_VERSIONS:
        return client_version
    return LATEST_PROTOCOL_VERSION
