/** Copyright (c) 2023-present Plane Software, Inc. and contributors. SPDX-License-Identifier: AGPL-3.0-only */
import { useEffect, useMemo, useState } from "react";
import useSWR from "swr";
import { Ban, Bot, CheckCircle2, ChevronDown, Loader2, Send, XCircle } from "lucide-react";
import { Button } from "@makeplane/propel/components/button";
import { Tooltip } from "@makeplane/propel/components/tooltip";
import { calculateTimeAgo, cn } from "@plane/utils";
import { agentDispatchService } from "@/services/integrations/agent-dispatch.service";
import type { AgentDispatchRun, DispatchEvent, DispatchStatus } from "@/services/integrations/agent-dispatch.service";

const INSTRUCTIONS_LIMIT = 2000;
const POLL_INTERVAL_MS = 5000;
const MAX_VISIBLE_RUNS = 3;
const MESSAGE_LIMIT = 2000;

const ACTIVE_STATUSES: ReadonlySet<DispatchStatus> = new Set(["dispatching", "active"]);

const STEPS: { key: DispatchStatus; label: string }[] = [
  { key: "dispatching", label: "Starting" },
  { key: "active", label: "Working" },
  { key: "completed", label: "Done" },
];

const STEP_INDEX: Record<DispatchStatus, number> = {
  dispatching: 0,
  active: 1,
  completed: 2,
  failed: -1,
  cancelled: -1,
};

type HTTPishError = { response?: { status?: number; data?: { error?: unknown; detail?: unknown } } };

function dispatchMessage(error: unknown): string {
  const value = error as HTTPishError;
  if (value?.response?.status === 403) return "You do not have permission to dispatch on this project.";
  const detail = value?.response?.data?.error ?? value?.response?.data?.detail;
  return typeof detail === "string" ? detail : "The agent could not start. Please try again.";
}

function eventLine(event: DispatchEvent): string {
  switch (event.type) {
    case "job.routed":
      return ["Routed to", [event.harness, event.model].filter(Boolean).join(" · ") || "auto"].join(" ");
    case "job.started":
      return event.branch ? `Agent working — ${event.branch}` : "Agent working";
    case "job.progress":
      return event.text || "";
    case "job.question":
      return `Agent asks: ${event.text || "(no text)"}`;
    case "job.preview_ready":
      return "Preview is ready.";
    case "job.completed":
      return event.summary ? `Finished: ${event.summary}` : "Finished.";
    case "job.failed":
      return `Failed: ${event.reason || "no reason given"}`;
    case "job.cancelled":
      return `Cancelled: ${event.reason || "by requester"}`;
    case "message.sent":
      return `You: ${event.text || ""}`;
    default:
      return event.text || event.type;
  }
}

function StepChip({ state, label, spinning = false }: { state: "done" | "current" | "todo"; label: string; spinning?: boolean }) {
  const styles =
    state === "current"
      ? "border-accent-subtle bg-accent-subtle font-semibold text-accent-primary"
      : state === "done"
        ? "border-subtle bg-layer-2 text-secondary"
        : "border-subtle text-tertiary";
  return (
    <span className={cn("inline-flex shrink-0 items-center gap-1 rounded-full border px-1.5 py-0.5 text-11 leading-4", styles)}>
      {state === "done" && <CheckCircle2 className="size-3" aria-hidden />}
      {spinning && <Loader2 className="size-3 animate-spin" aria-hidden />}
      {label}
    </span>
  );
}

function Connector() {
  return <span aria-hidden className="h-px w-3 shrink-0 border-t border-subtle" />;
}

function StatusStepper({ status }: { status: DispatchStatus }) {
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
            spinning={step.key === "active" && status === "active"}
          />
        </span>
      ))}
    </span>
  );
}

function RunRow({
  run,
  disabled,
  onAnswer,
  onCancel,
  answering,
  cancelling,
}: {
  run: AgentDispatchRun;
  disabled: boolean;
  onAnswer: (runId: string, questionId: string | undefined, text: string) => Promise<void>;
  onCancel: (runId: string) => Promise<void>;
  answering: boolean;
  cancelling: boolean;
}) {
  const [detailsOpen, setDetailsOpen] = useState(run.status === "dispatching" || run.status === "active");
  const [answer, setAnswer] = useState("");
  const [answerError, setAnswerError] = useState("");

  const failed = run.status === "failed";
  const events = run.events ?? [];
  // The newest unanswered question owns the reply box.
  const openQuestion = [...events].reverse().find((event) => event.type === "job.question");

  const send = async (questionId?: string) => {
    const text = answer.trim();
    if (!text) return;
    setAnswerError("");
    try {
      await onAnswer(run.id, questionId, text);
      setAnswer("");
    } catch (cause) {
      setAnswerError(dispatchMessage(cause));
    }
  };

  return (
    <li
      className={cn(
        "group rounded-md border border-subtle bg-layer-1 p-3 transition-colors hover:bg-layer-1-hover",
        failed && "bg-danger-subtle/40 hover:bg-danger-subtle/40"
      )}
    >
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <StatusStepper status={run.status} />
        <span className="ml-auto inline-flex shrink-0 items-center gap-2">
          {run.status !== "completed" && run.status !== "failed" && run.status !== "cancelled" && (
            <Button
              size="xs"
              stretch="auto"
              variant="ghost"
              label="Cancel"
              loading={cancelling}
              disabled={disabled}
              onClick={() => void onCancel(run.id)}
            />
          )}
          <Tooltip label={new Date(run.created_at).toLocaleString()} layout="stacked">
            <time dateTime={run.created_at} className="cursor-default text-11 text-tertiary">
              {calculateTimeAgo(run.created_at)}
            </time>
          </Tooltip>
        </span>
      </div>

      {run.instructions && <p className="mt-2 truncate text-12 text-tertiary">Instructions: {run.instructions}</p>}

      {events.length > 0 && (
        <ul className="mt-2 space-y-1 border-t border-subtle pt-2">
          {events.slice(-8).map((event, index) => (
            <li key={`${event.id}-${index}`} className="flex items-start gap-1.5 text-12 text-secondary">
              <span className="mt-1 size-1 shrink-0 rounded-full bg-tertiary" aria-hidden />
              <span className="min-w-0 flex-1 break-words">{eventLine(event)}</span>
            </li>
          ))}
        </ul>
      )}

      {openQuestion && run.status === "active" && (
        <div className="mt-2 rounded-md border border-accent-subtle bg-accent-subtle/40 p-2">
          <label htmlFor={`answer-${run.id}`} className="text-11 font-medium text-secondary">
            Reply to the agent
          </label>
          <div className="mt-1 flex items-center gap-1.5">
            <input
              id={`answer-${run.id}`}
              value={answer}
              maxLength={MESSAGE_LIMIT}
              placeholder={openQuestion.options?.length ? `e.g. ${openQuestion.options[0]}` : "Type your answer…"}
              className="min-w-0 flex-1 rounded-md border border-subtle bg-surface-1 px-2.5 py-1.5 text-12 text-primary focus:border-strong focus:outline-none"
              onChange={(event) => setAnswer(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && answer.trim()) void send(openQuestion.question_id);
              }}
            />
            <Button
              size="sm"
              stretch="auto"
              variant="primary"
              label="Send"
              icon={<Send className="size-3" aria-hidden />}
              loading={answering}
              disabled={disabled || !answer.trim()}
              onClick={() => void send(openQuestion.question_id)}
            />
          </div>
          {openQuestion.options && openQuestion.options.length > 0 && (
            <div className="mt-1.5 flex flex-wrap gap-1">
              {openQuestion.options.slice(0, 5).map((option) => (
                <button
                  key={option}
                  type="button"
                  disabled={answering || disabled}
                  className="rounded-full border border-subtle px-2 py-0.5 text-11 text-secondary hover:bg-layer-2 disabled:opacity-50"
                  onClick={() => void onAnswer(run.id, openQuestion.question_id, option).catch(() => undefined)}
                >
                  {option}
                </button>
              ))}
            </div>
          )}
          {answerError && (
            <p role="alert" className="mt-1.5 text-11 text-danger-primary">
              {answerError}
            </p>
          )}
        </div>
      )}

      {failed && (
        <p className="mt-2 flex items-start gap-1.5 rounded bg-danger-subtle px-2 py-1.5 text-12 text-danger-primary">
          <XCircle className="mt-0.5 size-3.5 shrink-0" aria-hidden />
          <span className="min-w-0 flex-1">
            {(events.filter((event) => event.type === "job.failed").pop()?.reason) || "The run failed."}
          </span>
        </p>
      )}

      {run.status === "cancelled" && <p className="mt-2 text-12 text-tertiary">This run was cancelled.</p>}

      <div className="mt-1.5 flex justify-end">
        <Button
          size="xs"
          stretch="auto"
          variant="ghost"
          label={detailsOpen ? "Hide details" : "Details"}
          icon={<ChevronDown className={cn("size-3 transition-transform", detailsOpen && "rotate-180")} aria-hidden />}
          onClick={() => setDetailsOpen((open) => !open)}
          aria-expanded={detailsOpen}
        />
      </div>
      <div className={cn("overflow-hidden transition-all duration-200", detailsOpen ? "opacity-100" : "max-h-0 opacity-0")}>
        <div className="space-y-1 border-t border-subtle pt-2">
          <p className="text-12 text-tertiary">
            <span className="text-secondary">Job:</span> {run.job_id || "not created yet"}
          </p>
          <p className="text-12 text-tertiary">
            <span className="text-secondary">Requested by:</span> {run.requester || "unknown"}
          </p>
        </div>
      </div>
    </li>
  );
}

function SkeletonCard() {
  return (
    <section className="rounded-lg border border-subtle p-4" aria-busy="true">
      <div className="flex items-start gap-2.5">
        <div className="size-7 shrink-0 animate-pulse rounded-md bg-layer-2" />
        <div className="min-w-0 flex-1 space-y-2">
          <div className="h-3.5 w-28 animate-pulse rounded bg-layer-2" />
          <div className="h-3 w-56 max-w-full animate-pulse rounded bg-layer-2" />
        </div>
      </div>
    </section>
  );
}

export function IssueAgentDispatch({
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
  const [answering, setAnswering] = useState(false);
  const [cancelling, setCancelling] = useState(false);

  const { data, error, isLoading, mutate } = useSWR(["agent-dispatch", workspaceSlug, projectId, issueId], () =>
    agentDispatchService.listDispatches(workspaceSlug, projectId, issueId)
  );

  const runs = useMemo(() => data ?? [], [data]);
  const hasActiveRun = runs.some((run) => ACTIVE_STATUSES.has(run.status));

  // Poll while a run is moving so the transcript tracks the agent live.
  useEffect(() => {
    if (!hasActiveRun) return;
    const interval = window.setInterval(() => {
      void mutate();
    }, POLL_INTERVAL_MS);
    return () => window.clearInterval(interval);
  }, [hasActiveRun, mutate]);

  useEffect(() => {
    if ((error as HTTPishError)?.response?.status === 403) {
      console.warn("[agent-dispatch] Dispatch list is not accessible for this project member.");
    }
  }, [error]);

  const unavailable = useMemo(() => {
    const code = (error as HTTPishError)?.response?.status;
    if (code === 404 || code === 503) return true;
    return Boolean(error) && runs.length === 0;
  }, [error, runs.length]);

  const replaceRun = (run: AgentDispatchRun) => {
    void mutate(
      (current) => (current ?? []).map((item) => (item.id === run.id ? run : item)),
      { revalidate: false }
    );
  };

  const startRun = async () => {
    setCreating(true);
    setCreateError("");
    try {
      const run = await agentDispatchService.createDispatch(workspaceSlug, projectId, issueId, instructions.trim() || undefined);
      void mutate((current) => [run, ...(current ?? [])], { revalidate: false });
      setComposerOpen(false);
      setInstructions("");
      void mutate();
    } catch (cause) {
      setCreateError(dispatchMessage(cause));
    } finally {
      setCreating(false);
    }
  };

  const answerRun = async (runId: string, _questionId: string | undefined, text: string) => {
    setAnswering(true);
    try {
      const run = await agentDispatchService.sendMessage(workspaceSlug, projectId, issueId, runId, text);
      replaceRun(run);
    } finally {
      setAnswering(false);
    }
  };

  const cancelRun = async (runId: string) => {
    setCancelling(true);
    try {
      const run = await agentDispatchService.cancelDispatch(workspaceSlug, projectId, issueId, runId);
      replaceRun(run);
    } finally {
      setCancelling(false);
    }
  };

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
            <h3 className="text-14 font-semibold text-primary">Dispatch to agent</h3>
            <p className="mt-0.5 text-12 text-tertiary">Send this issue to a coding agent and follow the run here.</p>
          </div>
        </div>
        {!composerOpen && (
          <Button
            size="sm"
            stretch="auto"
            variant="primary"
            label="Dispatch to agent"
            disabled={disabled || creating}
            onClick={() => setComposerOpen(true)}
          />
        )}
      </div>

      {composerOpen && (
        <div className="mt-3 rounded-md border border-subtle bg-layer-1 p-3">
          <label htmlFor={`dispatch-instructions-${issueId}`} className="text-12 font-medium text-secondary">
            Instructions for the agent <span className="font-normal text-tertiary">(optional)</span>
          </label>
          <textarea
            id={`dispatch-instructions-${issueId}`}
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
            <RunRow
              key={run.id}
              run={run}
              disabled={disabled}
              onAnswer={answerRun}
              onCancel={cancelRun}
              answering={answering}
              cancelling={cancelling}
            />
          ))}
        </ul>
      )}
    </section>
  );
}
