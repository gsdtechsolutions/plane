/** Copyright (c) 2023-present Plane Software, Inc. and contributors. SPDX-License-Identifier: AGPL-3.0-only */
import { useEffect, useMemo, useRef, useState } from "react";
import useSWR from "swr";
import { Ban, Bot, CheckCircle2, ChevronDown, ExternalLink, Loader2, XCircle } from "lucide-react";
import { Button } from "@makeplane/propel/components/button";
import { Tooltip } from "@makeplane/propel/components/tooltip";
import { calculateTimeAgo, cn } from "@plane/utils";
import { agentDelegationService } from "@/services/integrations/agent-delegation.service";
import type { DelegationRun, DelegationStatus } from "@/services/integrations/agent-delegation.service";

const INSTRUCTIONS_LIMIT = 2000;
const POLL_INTERVAL_MS = 5000;
const MAX_VISIBLE_RUNS = 3;

/** Statuses that mean the run is still moving — polling continues while any row carries one. */
const ACTIVE_STATUSES: ReadonlySet<DelegationStatus> = new Set(["queued", "claimed", "running"]);

const STEPS: { key: DelegationStatus; label: string }[] = [
  { key: "queued", label: "Queued" },
  { key: "claimed", label: "Claimed" },
  { key: "running", label: "Running" },
  { key: "pr_opened", label: "PR" },
  { key: "completed", label: "Done" },
];

const STEP_INDEX: Record<DelegationStatus, number> = {
  queued: 0,
  claimed: 1,
  running: 2,
  pr_opened: 3,
  completed: 4,
  failed: -1,
  cancelled: -1,
};

type HTTPishError = { response?: { status?: number } };

function delegationMessage(error: unknown): string {
  const value = error as { response?: { data?: { detail?: unknown; error?: unknown }; status?: number } };
  if (value?.response?.status === 403) return "You do not have permission to delegate on this project.";
  const detail = value?.response?.data?.detail ?? value?.response?.data?.error;
  return typeof detail === "string" ? detail : "The coding agent could not start. Please try again.";
}

function StepChip({
  state,
  label,
  spinning = false,
}: {
  state: "done" | "current" | "todo";
  label: string;
  spinning?: boolean;
}) {
  const styles =
    state === "current"
      ? "border-accent-subtle bg-accent-subtle font-semibold text-accent-primary"
      : state === "done"
        ? "border-subtle bg-layer-2 text-secondary"
        : "border-subtle text-tertiary";
  return (
    <span
      className={cn(
        "inline-flex shrink-0 items-center gap-1 rounded-full border px-1.5 py-0.5 text-11 leading-4",
        styles
      )}
    >
      {state === "done" && <CheckCircle2 className="size-3" aria-hidden />}
      {spinning && <Loader2 className="size-3 animate-spin" aria-hidden />}
      {label}
    </span>
  );
}

function Connector() {
  return <span aria-hidden className="h-px w-3 shrink-0 border-t border-subtle" />;
}

/** Queued → Claimed → Running → PR → Done; failed and cancelled leave the ladder for a terminal chip. */
function StatusStepper({ status }: { status: DelegationStatus }) {
  if (status === "failed") {
    return (
      <span className="inline-flex shrink-0 items-center gap-1 rounded-full border border-subtle bg-danger-subtle px-1.5 py-0.5 text-11 font-medium leading-4 text-danger-primary">
        <XCircle className="size-3" aria-hidden />
        Failed
      </span>
    );
  }
  if (status === "cancelled") {
    return (
      <span className="inline-flex shrink-0 items-center gap-1 rounded-full border border-subtle px-1.5 py-0.5 text-11 font-medium leading-4 text-tertiary">
        <Ban className="size-3" aria-hidden />
        Cancelled
      </span>
    );
  }
  const current = STEP_INDEX[status];
  return (
    <span className="inline-flex min-w-0 flex-wrap items-center gap-1">
      {STEPS.map((step, index) => (
        <span key={step.key} className="inline-flex items-center gap-1">
          {index > 0 && <Connector />}
          <StepChip
            label={step.label}
            state={index < current ? "done" : index === current ? "current" : "todo"}
            spinning={step.key === "running" && status === "running"}
          />
        </span>
      ))}
    </span>
  );
}

function DetailLine({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <p className="text-12 text-tertiary">
      <span className="text-secondary">{label}</span> {children}
    </p>
  );
}

function RunRow({ run }: { run: DelegationRun }) {
  const [detailsOpen, setDetailsOpen] = useState(false);
  const failed = run.status === "failed";

  return (
    <li
      className={cn(
        "group rounded-md border border-subtle bg-layer-1 p-3 transition-colors hover:bg-layer-1-hover",
        failed && "bg-danger-subtle/40 hover:bg-danger-subtle/40"
      )}
    >
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <StatusStepper status={run.status} />
        <span className="ml-auto inline-flex shrink-0 items-center">
          <Tooltip label={new Date(run.created_at).toLocaleString()} layout="stacked">
            <time dateTime={run.created_at} className="cursor-default text-11 text-tertiary">
              {calculateTimeAgo(run.created_at)}
            </time>
          </Tooltip>
        </span>
      </div>

      {run.status === "pr_opened" && run.pr_url && (
        <p className="mt-2 flex items-center gap-1.5 text-12">
          <a
            href={run.pr_url}
            target="_blank"
            rel="noopener noreferrer"
            title={run.pr_number ? `Draft pull request #${run.pr_number}` : "Draft pull request"}
            className="inline-flex items-center gap-1 font-medium text-accent-primary hover:underline"
          >
            Draft PR <ExternalLink className="size-3" aria-hidden />
          </a>
          {run.branch && <span className="truncate text-tertiary">{run.branch}</span>}
        </p>
      )}

      {run.status === "completed" && (
        <p className="mt-2 flex items-start gap-1.5 text-12 text-secondary">
          <CheckCircle2 className="mt-0.5 size-3.5 shrink-0 text-success-primary" aria-hidden />
          <span className="min-w-0 flex-1 truncate">{run.result_excerpt || "Run completed."}</span>
        </p>
      )}

      {failed && (
        <p className="mt-2 flex items-start gap-1.5 rounded bg-danger-subtle px-2 py-1.5 text-12 text-danger-primary">
          <XCircle className="mt-0.5 size-3.5 shrink-0" aria-hidden />
          <span className="min-w-0 flex-1 truncate">{run.error || "The run failed."}</span>
        </p>
      )}

      {run.status === "cancelled" && (
        <p className="mt-2 text-12 text-tertiary">This run was cancelled before it could finish.</p>
      )}

      {(run.status === "completed" || failed) && (
        <>
          <div className="mt-1.5 flex justify-end">
            <Button
              size="xs"
              stretch="auto"
              variant="ghost"
              label={detailsOpen ? "Hide" : "Details"}
              icon={
                <ChevronDown
                  className={cn("size-3 transition-transform", detailsOpen && "rotate-180")}
                  aria-hidden
                />
              }
              onClick={() => setDetailsOpen((open) => !open)}
              aria-expanded={detailsOpen}
            />
          </div>
          <div
            className={cn(
              "overflow-hidden transition-all duration-200",
              detailsOpen ? "max-h-48 opacity-100" : "max-h-0 opacity-0"
            )}
          >
            <div className="space-y-1 border-t border-subtle pt-2">
              {failed && <DetailLine label="Error:">{run.error || "No error details were reported."}</DetailLine>}
              {run.result_excerpt && <DetailLine label="Result:">{run.result_excerpt}</DetailLine>}
              {run.branch && <DetailLine label="Branch:">{run.branch}</DetailLine>}
              {run.runner_id && <DetailLine label="Runner:">{run.runner_id}</DetailLine>}
              {run.instructions && <DetailLine label="Instructions:">{run.instructions}</DetailLine>}
              {run.created_by && <DetailLine label="Requested by:">{run.created_by.email}</DetailLine>}
              {run.claimed_at && <DetailLine label="Claimed:">{calculateTimeAgo(run.claimed_at)}</DetailLine>}
              {run.started_at && <DetailLine label="Started:">{calculateTimeAgo(run.started_at)}</DetailLine>}
              {run.finished_at && <DetailLine label="Finished:">{calculateTimeAgo(run.finished_at)}</DetailLine>}
            </div>
          </div>
        </>
      )}
    </li>
  );
}

function SkeletonCard() {
  return (
    <section className="rounded-lg border border-subtle p-4" aria-busy="true">
      <div className="flex items-start gap-2.5">
        <div className="size-7 shrink-0 animate-pulse rounded-md bg-layer-2" />
        <div className="min-w-0 flex-1 space-y-2">
          <div className="h-3.5 w-24 animate-pulse rounded bg-layer-2" />
          <div className="h-3 w-56 max-w-full animate-pulse rounded bg-layer-2" />
        </div>
      </div>
    </section>
  );
}

export function IssueDelegation({
  workspaceSlug,
  projectId,
  issueId,
  disabled = false,
}: {
  workspaceSlug: string;
  projectId: string;
  issueId: string;
  disabled?: boolean;
}) {
  const [composerOpen, setComposerOpen] = useState(false);
  const [instructions, setInstructions] = useState("");
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState("");

  const { data, error, isLoading, mutate } = useSWR(["agent-delegations", workspaceSlug, projectId, issueId], () =>
    agentDelegationService.listDelegations(workspaceSlug, projectId, issueId)
  );

  const runs = useMemo(() => {
    const list = [...(data?.results ?? [])];
    list.sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());
    return list;
  }, [data]);

  const hasActiveRun = runs.some((run) => ACTIVE_STATUSES.has(run.status));

  // Poll every 5s while any run is queued/claimed/running; the interval is cleared the moment the
  // runs settle (and on unmount), so nothing leaks after the last row completes.
  useEffect(() => {
    if (!hasActiveRun) return;
    const interval = window.setInterval(() => {
      void mutate();
    }, POLL_INTERVAL_MS);
    return () => window.clearInterval(interval);
  }, [hasActiveRun, mutate]);

  // 403: the caller is not a project member — surface in the console, hide in the UI.
  useEffect(() => {
    if ((error as HTTPishError)?.response?.status === 403) {
      console.warn("[delegate] Agent delegation list is not accessible for this project member.");
    }
  }, [error]);

  const unavailable = useMemo(() => {
    const status = (error as HTTPishError)?.response?.status;
    // Endpoint missing (404) or the runner service is down (503): degrade to nothing at all.
    if (status === 404 || status === 503) return true;
    // Transient failures keep previously loaded runs visible instead of dropping the card.
    return Boolean(error) && runs.length === 0;
  }, [error, runs.length]);

  const mergeRun = (run: DelegationRun) => {
    void mutate(
      (current) => {
        const existing = current?.results ?? [];
        if (existing.some((item) => item.id === run.id)) return current;
        return { count: (current?.count ?? 0) + 1, results: [run, ...existing] };
      },
      { revalidate: false }
    );
  };

  const startRun = async () => {
    setCreating(true);
    setCreateError("");
    try {
      const trimmed = instructions.trim();
      const run = await agentDelegationService.createDelegation(
        workspaceSlug,
        projectId,
        issueId,
        trimmed || undefined
      );
      mergeRun(run); // optimistic row — the confirmed 201 response, shown before the next poll
      setComposerOpen(false);
      setInstructions("");
      void mutate(); // catch an early claim/transition ahead of the 5s tick
    } catch (cause) {
      setCreateError(delegationMessage(cause));
    } finally {
      setCreating(false);
    }
  };

  const createButton = (
    <Button
      size="sm"
      stretch="auto"
      variant="primary"
      label="Delegate to agent"
      disabled={disabled || creating}
      onClick={() => setComposerOpen(true)}
    />
  );

  if (unavailable) return null;
  if (isLoading && !data) return <SkeletonCard />;

  const counterTone = instructions.length >= INSTRUCTIONS_LIMIT ? "text-danger-primary" : "text-tertiary";

  return (
    <section className="rounded-lg border border-subtle p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-2.5">
          <span className="flex size-7 shrink-0 items-center justify-center rounded-md bg-accent-subtle text-accent-primary">
            <Bot className="size-4" aria-hidden />
          </span>
          <div className="min-w-0">
            <h3 className="text-14 font-semibold text-primary">Coding agent</h3>
            <p className="mt-0.5 text-12 text-tertiary">Delegate this issue to an autonomous coding agent.</p>
          </div>
        </div>
        {!composerOpen && createButton}
      </div>

      {composerOpen && (
        <div className="mt-3 rounded-md border border-subtle bg-layer-1 p-3">
          <label htmlFor={`delegation-instructions-${issueId}`} className="text-12 font-medium text-secondary">
            Instructions for the agent <span className="font-normal text-tertiary">(optional)</span>
          </label>
          <textarea
            id={`delegation-instructions-${issueId}`}
            value={instructions}
            maxLength={INSTRUCTIONS_LIMIT}
            rows={3}
            placeholder="Optional: extra context, files, acceptance criteria…"
            className="mt-1.5 w-full resize-y rounded-md border border-subtle bg-surface-1 px-2.5 py-2 text-13 text-primary focus:border-strong focus:outline-none"
            onChange={(event) => setInstructions(event.target.value)}
          />
          <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
            <span className={cn("text-11", counterTone)} aria-live="polite">
              {instructions.length}/{INSTRUCTIONS_LIMIT}
            </span>
            <div className="flex items-center gap-2">
              <Button
                size="sm"
                stretch="auto"
                variant="ghost"
                label="Cancel"
                disabled={creating}
                onClick={() => {
                  setComposerOpen(false);
                  setInstructions("");
                  setCreateError("");
                }}
              />
              <Button
                size="sm"
                stretch="auto"
                variant="primary"
                label="Start run"
                loading={creating}
                disabled={disabled}
                onClick={() => void startRun()}
              />
            </div>
          </div>
          {createError && (
            <p role="alert" className="mt-2 text-12 text-danger-primary">
              {createError}
            </p>
          )}
        </div>
      )}

      {runs.length > 0 && (
        <ul className="mt-3 space-y-2">
          {runs.slice(0, MAX_VISIBLE_RUNS).map((run) => (
            <RunRow key={run.id} run={run} />
          ))}
        </ul>
      )}
    </section>
  );
}
