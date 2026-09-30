# LANE-NOTES — ai-triage (feat/ai-triage)

## 1. Summary

AI triage on issue creation (Linear Triage Intelligence parity). A Celery task
`ai_triage.triage_issue` runs one LLM pass over a newly created issue (title +
description_stripped capped at 6k + state name) and proposes: labels (existing
project labels only, case-insensitive, max 2), an assignee (active project
members only), a priority (`urgent|high|medium|low` only), and a one-line
summary. Results are stored in a new fork model `AIIssueSuggestion`
(migration `0185_aiissuesuggestion`) as **pending reviewable chips** — nothing
is ever applied to the issue silently. Project members accept or dismiss each
chip via three new endpoints; acceptance writes label/assignee through-rows
DIRECTLY (`IssueLabel`/`IssueAssignee` ORM creates with explicit
`project=`/`workspace=`, never `.set()`) or updates `issue.priority` with
`update_fields`. Every step is audited through `plane.app.ai_ops.service.log_ai_action`
(actions `issue.triage_suggest`, `issue.triage_accept`, `issue.triage_dismiss`);
provider/parse failures degrade to an audited ERROR row, never a crash. Skips
the LLM entirely when pending suggestions already exist for the issue.

New files (all AGPL-headed):
- `apps/api/plane/db/models/ai_triage.py`
- `apps/api/plane/db/migrations/0185_aiissuesuggestion.py`
- `apps/api/plane/app/ai_triage/{__init__.py,tasks.py,api.py,urls.py}`
- `apps/api/plane/tests/contract/app/test_ai_triage.py`
- `apps/web/core/services/integrations/ai-triage.service.ts`
- `apps/web/core/components/ai-ops/triage-chips.tsx`

Shared files untouched per lane rules — the exact wiring lines are below.

## 2. EXACT coordinator wiring

### 2a. `plane/db/models/__init__.py`

Directly AFTER the existing seed line `from .ai_audit import AIActionAudit` (line 105):

```python
from .ai_triage import AIIssueSuggestion
```

IMPORTANT: without this line, `python manage.py makemigrations db --check --dry-run`
exits 1 with a spurious "- Delete model AIIssueSuggestion" drift (the model is
not registered anywhere during autodiscovery, while migration 0185 exists).
With this line the check exits 0 ("No changes detected in app 'db'") — verified
in-lane by temporarily applying the import.

### 2b. `plane/app/urls/__init__.py`

At the very end of the file, in the ai_ops style (after lines 75-76
`from plane.app.ai_ops.urls import ... / urlpatterns += ai_ops_urls`):

```python
from plane.app.ai_triage.urls import urlpatterns as ai_triage_urls
urlpatterns += ai_triage_urls
```

Routes exposed (all gated by the copied project_member helper: workspace
membership + active project membership role>=15; else 403; unknown ids 404):
- GET  `/api/workspaces/<slug>/projects/<project_id>/issues/<issue_id>/triage-suggestions/`
- POST `/api/workspaces/<slug>/projects/<project_id>/issues/<issue_id>/triage-suggestions/<sid>/accept/`
- POST `/api/workspaces/<slug>/projects/<project_id>/issues/<issue_id>/triage-suggestions/<sid>/dismiss/`

### 2c. `plane/celery.py`

AFTER the last existing task import (line 142
`import plane.app.slack_delivery.tasks  # noqa: F401,E402`):

```python
import plane.app.ai_triage.tasks  # noqa: F401,E402
```

(Task name `ai_triage.triage_issue`, default celery queue — no queue split needed.)

### 2d. Issue-create enqueue call site

File: `plane/app/views/issue/base.py` — class `IssueViewSet`, method `create`
(`def create(self, request, slug, project_id):` at line 405). Directly AFTER
the existing `serializer.save()` line (~line 416), insert exactly these two lines:

```python
from plane.app.ai_triage.tasks import maybe_enqueue_triage
maybe_enqueue_triage(serializer.data["id"], created=True)
```

(The local import keeps the change to exactly two lines; the helper no-ops for
updates and drafts since `created=True` is only passed here.)

### 2e. Mount the chips on the issue detail

File: `apps/web/core/components/issues/issue-detail-widgets/root.tsx`
(function `IssueDetailWidgets`; `workspaceSlug`, `projectId`, `issueId` are all
in scope as props there). Add the import beside the existing
`IssueDevelopment` import:

```tsx
import { AIIssueSuggestions } from "@/components/ai-ops/triage-chips";
```

and mount the row immediately AFTER the existing
`<IssueDevelopment workspaceSlug={workspaceSlug} projectId={projectId} issueId={issueId} disabled={disabled} />`
line (inside the `flex flex-col space-y-4` div, so it stacks with the widgets).
`disabled` is not needed: the component self-hides (404/403/503 render nothing)
and review actions are inherently per-user, not per-edit-mode.

The component (`AIIssueSuggestions`) takes `{ workspaceSlug, projectId, issueId }`,
is a mobx `observer`, uses SWR against `aiTriageService.getSuggestions`, shows
skeleton while loading, a muted "No AI suggestions" pill when the list is
empty, pending chips with check/X buttons (accept dims to a settled state,
dismiss removes), and renders NOTHING on API 403/404/503 (backend absent, no
access, LLM unconfigured). It also resolves label colors from the project
label store and assignee avatars from the member store.

### 2f. Optional cleanup once wiring lands

The contract tests self-mount the routes in a test-local root urlconf (fixture
`triage_routes` in `test_ai_triage.py`) because this lane must not edit the
shared urlconf. After 2b lands, the fixture simply double-registers identical
paths (harmless; resolution order unchanged) and can be kept or deleted.

## 3. Validation results

- `pytest plane/tests/contract/app/test_ai_triage.py --create-db -q` → **24 passed**, EXIT=0
  (task happy path incl. case-insensitive label match + fenced-JSON parse +
  audit row; unknown label / non-member email / invalid priority dropped;
  garbage JSON → audit ERROR, zero rows; IntelligenceError → audit ERROR, zero
  rows, no raise; pending-skip; missing-issue no-op; `maybe_enqueue_triage`
  only on created=True; GET ordering pending-first; accept label/assignee
  assert DIRECT through-rows exist WITH project/workspace columns; priority
  update + description untouched; summary informational-only; double-accept
  409; dismiss; non-member 403; unknown 404)
- `makemigrations db --check --dry-run` → MIG_EXIT=1 in-lane ONLY due to the
  missing shared import (see 2a; spurious delete drift); verified MIG_EXIT=0
  with the 2a line applied. Generated migration `0185_aiissuesuggestion`
  contains ONLY `CreateModel AIIssueSuggestion` on top of `0184_ai_action_audit`.
- `compileall` over all changed .py files → EXIT=0
- `tsc --noEmit` (web) → 123 errors, ALL in pre-existing untouched files
  (expected baseline range); **0 errors** in files touched by this lane.
