# AI feature wave — 2026-09-30

Index of the seven features in this wave and their application API endpoints.
Paths include the `/api/` prefix; `{slug}` is the workspace slug and all
`*_id` placeholders are UUIDs. Endpoints retain their trailing slash.

## 1. AI action audit trail

Workspace administrators can inspect AI actions, actors, models, results,
errors, and latency. The trail is read-only and supports filtering and search.

- `GET /api/workspaces/{slug}/ai-audit/`
- Pagination: `offset` and `limit` (default 50, maximum 200).
- Source: [AI operations](../apps/api/plane/app/ai_ops/api.py).

## 2. AI issue triage

New issues receive reviewable label, assignee, priority, and summary suggestions.
Suggestions remain pending until a project member accepts or dismisses them.

- `GET /api/workspaces/{slug}/projects/{project_id}/issues/{issue_id}/triage-suggestions/`
- `POST /api/workspaces/{slug}/projects/{project_id}/issues/{issue_id}/triage-suggestions/{sid}/accept/`
- `POST /api/workspaces/{slug}/projects/{project_id}/issues/{issue_id}/triage-suggestions/{sid}/dismiss/`
- `{sid}` is the suggestion UUID; access requires active project membership, role ≥15.
- Source: [Triage lane notes](lane-notes/ai-triage.md).

## 3. Coding agent delegation

Project members can queue issue work for a coding agent and inspect run status
and results. A separately configured runner claims work and reports progress.

- `GET /api/workspaces/{slug}/projects/{project_id}/issues/{issue_id}/agent-delegations/`
- `POST /api/workspaces/{slug}/projects/{project_id}/issues/{issue_id}/agent-delegations/`
- `GET /api/workspaces/{slug}/projects/{project_id}/issues/{issue_id}/agent-delegations/{run_id}/`
- `GET /api/agent-runner/health/`
- `POST /api/agent-runner/claim/`
- `POST /api/agent-runner/{run_id}/events/`
- Runner endpoints require `X-Runner-Key`, configured through `AGENT_RUNNER_KEY`.
- Source: [Delegation backend notes](lane-notes/delegate-backend.md).

## 4. Linked activity timeline

An issue timeline combines GitHub pull requests, reviews and commits, Asana
links and sync events, and linked Slack threads and replies. This is a read-only
integration context feature; it does not require an AI generation call.

- `GET /api/workspaces/{slug}/projects/{project_id}/issues/{issue_id}/linked-activity/`
- Supports `source`, `offset`, and `limit` filters for project members.
- Source: [Activity timeline notes](lane-notes/activity-timeline.md).

## 5. Integration sync health and AI explanation

Workspace administrators can inspect Asana, GitHub, and Slack connection health
and request a plain-language explanation of integration drift.

- `GET /api/workspaces/{slug}/integrations/sync-health/`
- Add `?explain=true` to request an AI explanation alongside the health snapshot.
- Explanation failures preserve the snapshot and return an `explain_error`.
- Source: [Sync health notes](lane-notes/sync-health.md).

## 6. Slack issue drafts and workspace Q&A

The `Create issue from thread` message shortcut opens a reviewable issue draft.
`/plane-ask` and `/plane ask ...` return ephemeral answers with issue references.
Both flows reuse the existing Slack delivery endpoints and signature validation.

- `POST /api/slack-delivery/interactivity/` — shortcut and modal submission.
- `POST /api/slack-delivery/commands/` — Q&A slash commands.
- Configure shortcut callback `create_issue_from_thread` and reconnect the bot
  with the required scopes, including `chat:write`.
- Source: [Slack asks notes](lane-notes/slack-asks.md).

## 7. Web workspace Q&A

Active workspace members can ask questions about issue titles, descriptions,
and comments from the `/{slug}/ask` page. Answers include clickable references;
questions with no matching issues return a message without calling the model.

- `POST /api/workspaces/{slug}/ask/`
- Body: `{"question": "What is blocked?", "project_id": "optional-project-uuid"}`.
- `question` must be 3–500 characters; omit `project_id` for workspace-wide search.
- Source: [Workspace Q&A notes](lane-notes/web-qa.md).

## Shared AI configuration

Generation uses server-side instance configuration: `LLM_API_KEY`, `LLM_MODEL`,
`LLM_PROVIDER`, and optional `LLM_BASE_URL`. Configure a key and model before
requesting AI output. AI actions use best-effort auditing; audit failures must
not break the originating feature.
