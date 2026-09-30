# Plane MCP Server

This fork ships a built-in [Model Context Protocol](https://modelcontextprotocol.io) (MCP) server, so any MCP-capable AI agent — ZCode, Claude Code, Cursor, Codex, Windsurf, … — can read and write your Plane workspace with Plane's own API keys.

## How it works

- **Endpoint:** `https://<your-plane-host>/mcp` (streamable HTTP, stateless — one URL, no stdio wrapper)
- **Auth:** any Plane API key, sent as `Authorization: Bearer <key>` (or the classic `X-API-Key: <key>` header)
- **Scoping:** the token's user and their project memberships decide what the agent can see; writes additionally require the project Member/Admin role, exactly like the external REST API
- **Audit & limits:** token usage is recorded (`last_used`, API activity log). The per-key rate limit that applies to `/api/v1` does **not** apply here — agents burst far past 60 calls/minute, and every request still requires a valid token

## Setup

1. In Plane: **Settings → API Tokens → Create new token** (workspace admins control who can mint tokens).
2. Point your MCP client at the endpoint with the token in a header.

> Keep tokens secret — an MCP config file with the token grants your workspace access to anything that reads it.

## Client configuration

### ZCode

```json
{
  "mcpServers": {
    "plane": {
      "type": "http",
      "url": "https://plane.example.com/mcp",
      "headers": {
        "Authorization": "Bearer plane_api_xxxxxxxxxxxx"
      }
    }
  }
}
```

### Claude Code

```bash
claude mcp add --transport http plane https://plane.example.com/mcp \
  --header "Authorization: Bearer plane_api_xxxxxxxxxxxx"
```

### Cursor

`.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "plane": {
      "url": "https://plane.example.com/mcp",
      "headers": {
        "Authorization": "Bearer plane_api_xxxxxxxxxxxx"
      }
    }
  }
}
```

### Codex CLI

`~/.codex/config.toml` (Codex ≥ 0.42 supports streamable-HTTP servers):

```toml
[mcp_servers.plane]
url = "https://plane.example.com/mcp"
http_headers = { "Authorization" = "Bearer plane_api_xxxxxxxxxxxx" }
```

### Generic / Inspector

```bash
npx @modelcontextprotocol/inspector --cli \
  --url https://plane.example.com/mcp \
  --transport http \
  --header "Authorization: Bearer plane_api_xxxxxxxxxxxx" \
  --method tools/list
```

## Tools

| Tool | Purpose |
|---|---|
| `plane_list_projects` | Projects the token's user can access |
| `plane_list_states` | Workflow states (columns) of a project |
| `plane_list_members` | Workspace / project members with emails |
| `plane_list_labels` | Labels defined on a project |
| `plane_list_issues` | Search & filter work items (state, priority, assignee, label, text) |
| `plane_get_issue` | Full work item: description, comments, sub-issues, deep link |
| `plane_create_issue` | Create a work item (description, state, assignees, labels, parent) |
| `plane_update_issue` | Update title/description/priority/state/assignees/labels |
| `plane_add_comment` | Comment on a work item |
| `plane_delete_issue` | Permanently delete a work item |

### Agent conventions

- Work items are addressed by human refs like `PROJ-14`; projects by identifier (`PROJ`), name, or UUID.
- Assignees are workspace user emails (or `me`); states are matched by name, case-insensitive.
- Every tool accepts an optional `workspace_slug`; it defaults to the token's workspace.
- Missing labels are created on demand; unknown states/projects return actionable errors listing valid options.

## Notes

- The endpoint lives inside the Django API (no extra service). Caddy routes `/mcp*` to the api container in the Coolify compose — redeploying the stack is all that's needed.
- Authentication failures return `401`; there is no OAuth flow — use API keys.
- `GET /mcp` returns `405` (no server-initiated SSE streams are offered).
