# LANE-NOTES.md — activity-timeline lane

Branch: `feat/activity-timeline` (base `gsd/stage` @ 4ca5f12ae, includes the ai-ops seed).
All work confined to new, self-contained files; no shared file was edited.

## Summary

Cross-platform linked-activity timeline (Rovo Teamwork Graph parity):

- Backend `GET /api/workspaces/<slug>/projects/<project_id>/issues/<issue_id>/linked-activity/`
  (read-only, no migration). Aggregates per issue, newest first, merging all three
  integration domains:
  - **github**: `GitHubIssueLink` → linked PRs → `pr_opened` (`remote_created_at`),
    `pr_merged` (`merged_at`, meta carries `base_ref`), `pr_closed` (`remote_closed_at`
    when state closed and not merged); `GitHubCheckRun` for those PRs → `check_completed`
    (`completed_at`, conclusion in meta with `failed` bool); `GitHubPullRequestReview`
    → `review` (state + reviewer login); `GitHubCommitIssueLink` → `commit`
    (short sha, first message line, author name/login).
  - **asana**: `AsanaTaskLink` → `task_linked` (url from connection workspace gid,
    fallback project gid); `AsanaCommentLink` → `comment_synced` (direction in meta);
    `AsanaSyncLog` (project scope, `issue=issue` rows plus issue-less project rows) →
    `sync_ok` / `sync_warning` (`skipped`/`conflict`) / `sync_error`.
  - **slack**: `SlackIssueLink` → `thread_linked` (`posted_at` or fallbacks, slack
    permalink when `team_domain` set); thread replies (`thread_ts` matching the linked
    message per mapping, non-deleted) → `slack_message` (actor = user_id, first text
    line as title).
  - Event shape: `{id: "<source>-<type>-<model pk>", source, type, title (<=200),
url (https only or null), actor (or null), timestamp (ISO), meta}`.
  - Per-source candidate cap 100; merged sort desc by timestamp; manual offset/limit
    slicing (default 50, cap 200, ai_ops idiom); per-row and per-source try/except —
    broken rows are skipped, never a 500; timestamp-less rows are dropped.
  - Permission idiom copied from `plane/app/slack_delivery/api.py` `project_member`.
- Web: `linked-activity.service.ts` (APIService subclass) + `timeline.tsx`
  (vertical rail timeline, source-colored glyph chips, skeleton rows, empty state,
  "Load more", dark-mode-safe custom tokens).

## Files added (all new)

- `apps/api/plane/app/linked_activity/__init__.py`
- `apps/api/plane/app/linked_activity/api.py`
- `apps/api/plane/app/linked_activity/urls.py`
- `apps/api/plane/tests/contract/app/test_linked_activity.py`
- `apps/web/core/services/integrations/linked-activity.service.ts`
- `apps/web/core/components/linked-activity/timeline.tsx`
- `LANE-NOTES.md` (this file)

## EXACT coordinator wiring (shared files — not edited by this lane)

### 1. `apps/api/plane/app/urls/__init__.py` (ai_ops pattern, bottom of file)

Append after the existing `urlpatterns += ai_ops_urls` block:

```python
from plane.app.linked_activity.urls import urlpatterns as linked_activity_urls
urlpatterns += linked_activity_urls
```

Mounted route becomes:
`/api/workspaces/<str:slug>/projects/<uuid:project_id>/issues/<uuid:issue_id>/linked-activity/`
(`plane/app/linked_activity/urls.py` already contains exactly this `path()`).

### 2. Issue-detail mount suggestion — `apps/web/core/components/issues/issue-detail-widgets/root.tsx`

In scope there: `IssueDetailWidgets` already renders `IssueDetailWidgetActionButtons`,
`IssueDetailWidgetCollapsibles` and `IssueDevelopment` in a `flex flex-col space-y-4`
stack (IssueDevelopment is imported and mounted at the bottom of that stack). Add
alongside `IssueDevelopment`:

```tsx
import { IssueLinkedActivity } from "@/components/linked-activity/timeline";
```

and inside the `<div className="flex flex-col space-y-4">`, right after the
`<IssueDevelopment ... />` line:

```tsx
<IssueLinkedActivity workspaceSlug={workspaceSlug} projectId={projectId} issueId={issueId} />
```

Notes: the widget self-hides on 403 (project guests), needs no `disabled` prop
(read-only), and revalidates manually via its own SWR key.

## Validation results

- Contract tests: `plane/tests/contract/app/test_linked_activity.py`
  - **5 passed** (0 failed) — `EXIT=0`
  - log: `/tmp/timeline-pytest.log` (env: `/tmp/api-env-new.env` + `/tmp/timeline-db.env`)
  - Coverage: cross-source desc ordering + event shape keys + count (11 events across
    github/asana/slack with exact type order asserted); `?source=github` / `?source=asana`
    filters; `?offset=1&limit=2` slicing; non-member 403 (both a non-project workspace
    member and an unauthenticated-workspace stranger); tolerance — a `GitHubCommit` with
    null `committed_at` still yields 200 (fallback to `updated_at`, no crash).
  - Note: the tests carry a local `pytest.mark.urls(...)` routing the new path
    standalone, so they pass **before** the coordinator wires `plane/app/urls/__init__.py`
    and keep passing after (Django first-match still finds the same path).

## Deviations / findings

- **dayjs is NOT resolvable from `apps/web`** in this worktree (not a declared
  dependency of any workspace package; `apps/web/node_modules/dayjs` absent under pnpm
  strict layout). Used the app's existing relative-time helper
  `calculateTimeAgo` from `@plane/utils` (date-fns based, used across web components)
  instead — zero new dependencies, same visual result. Absolute timestamp still
  available via the caption `title` attribute.
- Slack message permalinks mirror `plane/app/slack_delivery/services.py::message_url`
  logic inline (kept the module self-contained instead of importing across app packages).
- GitHub/Asana URL builders (`github_web_base`, `asana_task_url`) likewise inlined;
  shapes match the existing `github_delivery` services (`web_base`).
- `tsc --noEmit` for web: 123 errors, **all pre-existing baseline in untouched files**
  (expected 30-123 range); `grep` confirms **0 errors** in
  `core/services/integrations/linked-activity.service.ts` and
  `core/components/linked-activity/timeline.tsx`. Log: `/tmp/timeline-tsc.log`.
- `compileall` on the new python files: clean.
