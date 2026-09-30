# LANE-NOTES — delegate-web

## Summary

Built the "Delegate to coding agent" issue-detail UI (GitHub Copilot coding-agent / Agent HQ parity) as
two new files against the frozen backend contract. No existing files were edited; no new dependencies.

- `apps/web/core/services/integrations/agent-delegation.service.ts` — `AgentDelegationService extends APIService`
  (constructor `super(API_BASE_URL)`, URL builder mirroring `slack-delivery.service.ts`) with
  `createDelegation(workspaceSlug, projectId, issueId, instructions?)` (POST, body `{ instructions }` only when
  non-empty), `listDelegations(...)` (GET → `{count, results}` newest first), `getDelegation(..., runId)`.
  Exports `DelegationRun`, `DelegationStatus`, `DelegationList` types and the `agentDelegationService` singleton.
- `apps/web/core/components/ai-ops/delegation.tsx` — `IssueDelegation` card: Bot glyph + "Coding agent" title +
  muted caption; primary "Delegate to agent" button opens an inline composer (NOT a portal modal) with optional
  instructions textarea (placeholder "Optional: extra context, files, acceptance criteria…", 2000-char cap with
  live `n/2000` counter that turns red at the cap), Cancel + "Start run" (propel Button `loading` state).
  On 201 the returned run is merged into state immediately (optimistic row, deduped by id on later polls), the
  composer closes, and one immediate revalidation is fired. Polling: `setInterval` → `mutate()` every 5s while
  any run is queued/claimed/running; the effect's cleanup clears the interval when the runs settle AND on unmount
  (no leaks). Run rows: newest-first defensive sort, last 3; status stepper chips Queued → Claimed → Running → PR
  → Done with hairline connectors, current step accent-highlighted, spinner glyph on the Running chip; `pr_opened`
  renders a "Draft PR ↗" link (pr_url, `target="_blank" rel="noopener noreferrer"`); completed shows green check
  - result_excerpt behind a Details toggle (max-h/opacity fade); failed shows a danger-tinted row + error excerpt
  - Details (full error, branch, runner, requester, claimed/started/finished via `calculateTimeAgo`); cancelled
    is muted with a Ban glyph. States: skeleton while first load, button-only when zero runs, `404`/`503` render
    NOTHING, `403` renders nothing after a single `console.warn`, other errors keep loaded rows visible (graceful).
    Relative times: `calculateTimeAgo` from `@plane/utils`; absolute time in a propel `Tooltip` on the timestamp.

## Coordinator wiring (EXACT)

In `apps/web/core/components/issues/issue-detail-widgets/root.tsx` the props are destructured directly at
`const { workspaceSlug, projectId, issueId, disabled, renderWidgetModals = true, issueServiceType, hideWidgets } = props;`
— no hooks needed to obtain them. Add the import next to the existing IssueDevelopment import (line 8):

```tsx
import { IssueDelegation } from "@/components/ai-ops/delegation";
```

and mount it immediately after `<IssueDevelopment … />` (line 56) inside the `<div className="flex flex-col space-y-4">`:

```tsx
<IssueDevelopment workspaceSlug={workspaceSlug} projectId={projectId} issueId={issueId} disabled={disabled} />
<IssueDelegation workspaceSlug={workspaceSlug} projectId={projectId} issueId={issueId} disabled={disabled} />
```

`disabled` (already in scope) gates only the delegate action; run history stays visible when disabled.

## Deviation from the brief (verified, deliberate)

The brief's class vocabulary `bg-custom-*` / `text-custom-*` / `border-custom-*` and `text-xs` was written against
the pre-propel token set. In this fork, propel's `@theme` block does `--color-*: initial;` and `--text-*: initial;`
(`node_modules/@makeplane/propel/dist/styles/variables.css`) and defines NO `--color-custom-*` tokens, so those
classes are dead — they emit nothing (the fork's own `packages/tailwind-config/AGENTS.md` documents the current
system: canvas/surface/layer, `text-primary|secondary|tertiary`, `border-subtle`). Only legacy files
(e.g. `asana-sync-section.tsx`) still carry them. To keep the card actually styled and dark-mode safe, this lane
used the exact vocabulary of the mount-neighbour `core/components/github-delivery/development.tsx`:
`border-subtle`, `bg-layer-1`(+`-hover`), `bg-layer-2`, `bg-surface-1`, `bg-accent-subtle`, `text-accent-primary`,
`text-primary/secondary/tertiary`, `bg-danger-subtle`, `text-danger-primary`, `text-success-primary`,
`focus:border-strong`, `text-11/12/13/14`. Hover = `hover:bg-layer-1-hover` (propel's subtle layer hover); focus
ring = propel Button's own + `focus:border-strong focus:outline-none` on the raw textarea (matches house textareas).

## Notes for the integration lane

- Component is a plain function (no mobx `observer`): no store reads — member gating comes from the API's own
  403 (hidden + `console.warn`), so nothing to wire beyond the three slugs/ids + `disabled`.
- SWR key `["agent-delegations", workspaceSlug, projectId, issueId]`; revalidation only via the in-component
  interval + post-create `mutate()` (no `refreshInterval`), so it never polls when the work is settled.
- `DelegationRun`/`DelegationStatus`/`DelegationList` are exported from the service file if other lanes
  (e.g. a project-level run list) need the same shapes.

## Validation record

- `corepack pnpm@9.15.0 -F web exec tsc --noEmit` → EXIT=1 with **123 errors, all pre-existing stale-dist
  artifacts** (paths like `app/layout.ts`, `core/components/editor/**` — dist `.ts` files not touched by this
  lane); `grep "agent-delegation\|ai-ops" /tmp/delegateweb-tsc.log` → **zero matches**: no errors in any file
  from `git diff --name-only` (only the two new untracked files exist in the tree).
- Icons verified present in this `lucide-react` (Bot, Ban, CheckCircle2, XCircle, ExternalLink, Loader2,
  ChevronDown). propel `Button` used per its d.ts (`label`/`size`/`stretch`/`variant`, `loading`, no children);
  propel `Tooltip` used per its d.ts (`label`, single-ReactElement child, `layout="stacked"`).
