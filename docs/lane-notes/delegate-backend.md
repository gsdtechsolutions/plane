# delegate-backend

Implemented issue delegation with a durable DelegationRun model, member-scoped
create/list/detail endpoints, authenticated runner health/claim/event endpoints,
escaped issue comments and best-effort AIActionAudit records. Claims lock the
oldest available queued row; events lock the run and validate ownership and legal
transitions. Repeated event acknowledgements do not duplicate comments or audits.

The standalone runner polls Plane, creates an isolated worktree from gsd/stage,
runs Codex with a timeout, commits changes, pushes the agent branch and opens a
draft PR with gh. It reports outcomes and removes its worktree in finally.
No runner jobs, pushes, PRs, staging or commits were executed during this lane.

## Files

- apps/api/plane/db/models/agent_delegation.py
- apps/api/plane/db/migrations/0187_agent_delegation.py
- apps/api/plane/app/agent_delegation/**init**.py
- apps/api/plane/app/agent_delegation/api.py
- apps/api/plane/app/agent_delegation/urls.py
- apps/api/plane/tests/contract/app/test_agent_delegation.py
- scripts/agent_runner.py
- scripts/test_agent_runner.py
- LANE-NOTES.md

## Exact shared wiring for the coordinator

Append to apps/api/plane/db/models/**init**.py:

```python
from .agent_delegation import DelegationRun
```

Append to apps/api/plane/app/urls/**init**.py, matching the ai_ops pattern:

```python
from plane.app.agent_delegation.urls import urlpatterns as agent_delegation_urls
urlpatterns += agent_delegation_urls
```

No Celery or settings wiring is needed. The runner is a separate host process.
The shared files above, plane/celery.py and plane/settings/\* were not edited.

The hand-written migration is 0187_agent_delegation with dependency
[("db", "0184_ai_action_audit")]. The coordinator may renumber it to 0186 when
integrating the parallel lanes and must reconcile the final migration chain.
Indexes are implicit Django db_index indexes; no custom index names were added.

## API / runner contract

- Runner requests require X-Runner-Key; configure AGENT_RUNNER_KEY through instance
  configuration or the environment/settings fallback. Invalid and unset keys
  return the same 403 response.
- Event JSON includes runner_id in addition to status and the optional result
  fields. It must match the value supplied when the run was claimed.
- Claim response: {id, status, issue: {...}}. Suggested branch and repository hint
  are issue.branch and issue.repo. The issue display uses Issue.sequence_id.
  issue.issue_url uses settings.WEB_URL when available; the runner falls back to
  PLANE_API_BASE plus the issue path.
- Normal lifecycle: claimed -> running -> pr_opened -> completed. A running run
  without a PR can complete directly for the required no-changes outcome. Runs
  with a PR must record pr_opened first. Active runs can fail or cancel; terminal
  timestamps include cancellation. pr_opened requires a PR URL.
- Runner environment: PLANE_API_BASE (http://localhost:8000), AGENT_RUNNER_KEY
  (required), PLANE_REPO_DIR (/workspace/gsd-plane/plane), CODEX_BIN (codex),
  POLL_SECONDS (15), RUN_TIMEOUT_SECONDS (3600).
- Use the main clone API venv's Python to launch scripts/agent_runner.py; it
  provides httpx. The host needs git, Codex and authenticated gh.
- Brief files are removed after each run; protected temporary output logs remain
  under /tmp/gsd-agent-output-\* for diagnosis. No requeue/lease expiry is included:
  a host crash can leave a claimed/running run requiring operator intervention.

## Validation

Requested contract command against final code: 12 passed, 12 warnings, exit 0
in 259.83 seconds (/tmp/delegate-pytest.log). Warnings concern the existing missing
collected-static directory, not this lane's functionality.

```bash
cd /workspace/gsd-plane/wt/delegate-backend/apps/api
while IFS= read -r -d '' kv; do export "$kv"; done < /tmp/api-env-new.env
export SLACK_APP_BASE_URL=
/workspace/gsd-plane/plane/apps/api/.venv/bin/python -m pytest plane/tests/contract/app/test_agent_delegation.py --create-db -q > /tmp/delegate-pytest.log 2>&1; echo EXIT=$?
```

makemigrations db --check --dry-run: exit 0, "No changes detected in app 'db'"
(/tmp/delegate-migrations.log). The existing GitHubMentionSearch.issue ForeignKey
unique=True warning remains unchanged.

The test fixture uses test_plane_delegate_backend rather than test_plane. The
first run encountered a concurrent shared test database replacement after its
first passing test; a lane-specific test database avoids that collision while
retaining the exact requested pytest command. ROOT_URLCONF is overridden only
inside the test module to mount the lane's real URLs at /api/.

Since the shared model import must remain untouched, migration verification uses
PYTHONPATH=/tmp/delegate-migration-bootstrap:/workspace/gsd-plane/wt/delegate-backend/apps/api.
That temporary sitecustomize.py wraps django.setup and imports
plane.db.models.agent_delegation after normal setup, simulating the coordinator's
one-line model registration without editing any shared file. The original
production settings and supplied environment remain in use. After coordinator
wiring, this bootstrap is unnecessary.

Offline runner tests: 6 passed, exit 0 (/tmp/delegate-runner-pytest.log).
Compileall on all added Python files: exit 0. Runner ast.parse: exit 0 using
the main clone venv Python (the bare python command is absent on this host).
Ruff format/check for lane code: exit 0. No full repository suite or live runner
execution was performed.
