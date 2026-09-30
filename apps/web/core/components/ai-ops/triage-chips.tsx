/** Copyright (c) 2023-present Plane Software, Inc. and contributors. SPDX-License-Identifier: AGPL-3.0-only */
import { useEffect, useMemo, useState } from "react";
import { Check, Sparkles, X } from "lucide-react";
import useSWR from "swr";
import { observer } from "mobx-react";
import { Avatar } from "@makeplane/propel/components/avatar";
import { PriorityIcon } from "@plane/blocks/icons";
import { getFileURL } from "@plane/utils";
// hooks
import { useLabel } from "@/hooks/store/use-label";
import { useMember } from "@/hooks/store/use-member";
// services
import {
  aiTriageHiddenStatus,
  aiTriageService,
  AI_TRIAGE_HIDDEN_STATUSES,
  type Suggestion,
} from "@/services/integrations/ai-triage.service";

/** Best-effort string read from the backend's kind-shaped payload. */
function payloadString(suggestion: Suggestion, key: string): string {
  const value = suggestion.payload?.[key];
  return typeof value === "string" ? value : "";
}

/** Class vocabulary mirrors github-delivery/dev-chip.tsx: border-subtle +
 * bg-surface-1 chips stay readable in light and dark themes. */
const CHIP_CLASS =
  "inline-flex shrink-0 items-center gap-1 rounded-full border border-subtle bg-surface-1 px-1.5 py-px text-10 text-secondary";

const ACTION_BUTTON_CLASS =
  "grid size-3.5 shrink-0 place-items-center rounded-full text-tertiary hover:bg-surface-1 hover:text-primary transition-colors";

function SuggestionSkeleton() {
  return (
    <span className="inline-flex items-center gap-1" aria-hidden>
      <span className="h-3.5 w-16 animate-pulse rounded-full bg-surface-1" />
      <span className="h-3.5 w-12 animate-pulse rounded-full bg-surface-1" />
    </span>
  );
}

const LabelChip = observer(function LabelChip({
  projectId,
  suggestion,
}: {
  // accepted from the parent for API symmetry; the chip itself resolves color via the label store
  workspaceSlug?: string;
  projectId: string;
  suggestion: Suggestion;
}) {
  const { getProjectLabels } = useLabel();
  const name = payloadString(suggestion, "name");
  // Resolve the label color against existing project labels (case-insensitive);
  // the dot takes the label color, the pill itself stays theme-safe.
  const color = useMemo(() => {
    if (!name) return null;
    const needle = name.toLowerCase();
    const label = (getProjectLabels(projectId) ?? []).find((item) => item.name.toLowerCase() === needle);
    return label?.color || null;
  }, [getProjectLabels, projectId, name]);
  return (
    <span className={CHIP_CLASS} title={`AI suggests label: ${name}`}>
      <span
        className="size-1.5 shrink-0 rounded-full"
        style={color ? { backgroundColor: color } : undefined}
        aria-hidden
      />
      <span className="max-w-32 truncate">{name || "Label"}</span>
    </span>
  );
});

const AssigneeChip = observer(function AssigneeChip({ suggestion }: { suggestion: Suggestion }) {
  const { getUserDetails } = useMember();
  const email = payloadString(suggestion, "email");
  const userId = payloadString(suggestion, "user_id");
  const member = userId ? getUserDetails(userId) : undefined;
  const name = member?.display_name || member?.first_name || email || "Assignee";
  return (
    <span className={CHIP_CLASS} title={`AI suggests assignee: ${name}${email ? ` (${email})` : ""}`}>
      {member ? (
        <Avatar alt={name} fallback={name[0]?.toUpperCase()} src={getFileURL(member.avatar_url ?? "")} size="xs" />
      ) : (
        <span className="grid size-2.5 shrink-0 place-items-center rounded-full bg-surface-2 text-[8px] font-medium uppercase">
          {name[0]}
        </span>
      )}
      <span className="max-w-32 truncate">{name}</span>
    </span>
  );
});

function PriorityChip({ suggestion }: { suggestion: Suggestion }) {
  const priority = payloadString(suggestion, "priority");
  const known = ["urgent", "high", "medium", "low", "none"];
  const value = known.includes(priority) ? (priority as "urgent" | "high" | "medium" | "low" | "none") : "none";
  return (
    <span className={CHIP_CLASS} title={`AI suggests priority: ${value}`}>
      <PriorityIcon priority={value} className="size-2.5" />
      <span className="capitalize">{value}</span>
    </span>
  );
}

function SummaryChip({ suggestion }: { suggestion: Suggestion }) {
  const summary = payloadString(suggestion, "summary");
  if (!summary) return null;
  return (
    <span
      title={summary}
      className="inline-flex max-w-96 min-w-0 shrink items-center gap-1 truncate text-11 text-tertiary italic"
    >
      <Sparkles className="size-2.5 shrink-0" aria-hidden />
      <span className="truncate">{summary}</span>
    </span>
  );
}

function SuggestionChip({
  workspaceSlug,
  projectId,
  suggestion,
  busy,
  onAccept,
  onDismiss,
}: {
  projectId: string;
  suggestion: Suggestion;
  busy: boolean;
  onAccept: (suggestion: Suggestion) => void;
  onDismiss: (suggestion: Suggestion) => void;
}) {
  const pending = suggestion.status === "pending";
  const settled = suggestion.status !== "pending";
  return (
    <span
      className={`inline-flex shrink-0 items-center gap-1 ${settled ? "opacity-60" : ""}`}
      title={
        pending ? "AI suggestion — review before applying" : `AI suggestion ${suggestion.status} by you or a teammate`
      }
    >
      {suggestion.kind === "label" && (
        <LabelChip workspaceSlug={workspaceSlug} projectId={projectId} suggestion={suggestion} />
      )}
      {suggestion.kind === "assignee" && <AssigneeChip suggestion={suggestion} />}
      {suggestion.kind === "priority" && <PriorityChip suggestion={suggestion} />}
      {suggestion.kind === "summary" && <SummaryChip suggestion={suggestion} />}
      {pending && !busy && (
        <>
          <button
            type="button"
            className={ACTION_BUTTON_CLASS}
            title="Accept suggestion"
            aria-label="Accept suggestion"
            onClick={() => onAccept(suggestion)}
          >
            <Check className="size-2.5" aria-hidden />
          </button>
          <button
            type="button"
            className={ACTION_BUTTON_CLASS}
            title="Dismiss suggestion"
            aria-label="Dismiss suggestion"
            onClick={() => onDismiss(suggestion)}
          >
            <X className="size-2.5" aria-hidden />
          </button>
        </>
      )}
    </span>
  );
}

/**
 * Compact "AI Triage" chip row for the issue detail: reviewable label /
 * assignee / priority / summary suggestions produced by the async triage pass.
 *
 * Renders nothing while the backend is absent (404), the viewer lacks access
 * (403), or the instance has no LLM configured (503) — the row is best-effort
 * by design and must never block the issue detail.
 *
 * Mount: see LANE-NOTES.md (issue-detail-widgets/root.tsx, beside IssueDevelopment).
 */
export const AIIssueSuggestions = observer(function AIIssueSuggestions({
  workspaceSlug,
  projectId,
  issueId,
}: {
  workspaceSlug: string | undefined;
  projectId: string | undefined;
  issueId: string;
}) {
  const { fetchProjectLabels } = useLabel();
  const [busyIds, setBusyIds] = useState<string[]>([]);
  const [removedIds, setRemovedIds] = useState<string[]>([]);

  const { data, error, isLoading, mutate } = useSWR(
    workspaceSlug && projectId ? ["ai-triage-suggestions", workspaceSlug, projectId, issueId] : null,
    () => aiTriageService.getSuggestions(workspaceSlug as string, projectId as string, issueId),
    { revalidateOnFocus: false }
  );

  // Existing labels back the label-chip color lookup.
  useEffect(() => {
    if (workspaceSlug && projectId) void fetchProjectLabels(workspaceSlug, projectId);
  }, [workspaceSlug, projectId, fetchProjectLabels]);

  // Backend absent, no access, or LLM unconfigured: silently hide.
  const hiddenStatus = error ? aiTriageHiddenStatus(error) : undefined;
  if (hiddenStatus && AI_TRIAGE_HIDDEN_STATUSES.includes(hiddenStatus)) return null;
  if (error) return null; // any other failure: the row stays out of the way
  if (isLoading) return <SuggestionSkeleton />;

  // Dismissed chips drop out immediately; accepted ones flip to the settled dim state.
  const suggestions = (data ?? []).filter((suggestion) => !removedIds.includes(suggestion.id));
  if (suggestions.length === 0) {
    return (
      <span className="inline-flex shrink-0 items-center gap-1 rounded-full border border-subtle bg-surface-1 px-1.5 py-px text-10 text-tertiary">
        <Sparkles className="size-2.5" aria-hidden />
        No AI suggestions
      </span>
    );
  }

  const run = (suggestion: Suggestion, action: "accept" | "dismiss") => {
    if (!workspaceSlug || !projectId) return;
    setBusyIds((current) => [...current, suggestion.id]);
    const request =
      action === "accept"
        ? aiTriageService.acceptSuggestion(workspaceSlug, projectId, issueId, suggestion.id)
        : aiTriageService.dismissSuggestion(workspaceSlug, projectId, issueId, suggestion.id);
    void request
      .then(async () => {
        if (action === "dismiss") setRemovedIds((current) => [...current, suggestion.id]);
        await mutate();
        return null;
      })
      .catch(() => {
        /* the chips are review-only affordances; failures leave the row untouched */
      })
      .finally(() => setBusyIds((current) => current.filter((id) => id !== suggestion.id)));
  };

  return (
    <span className="inline-flex min-w-0 flex-wrap items-center gap-1.5" aria-label="AI triage suggestions">
      <Sparkles className="size-3 shrink-0 text-tertiary" aria-hidden />
      {suggestions.map((suggestion) => (
        <SuggestionChip
          key={suggestion.id}
          workspaceSlug={workspaceSlug as string}
          projectId={projectId as string}
          suggestion={suggestion}
          busy={busyIds.includes(suggestion.id)}
          onAccept={(row) => run(row, "accept")}
          onDismiss={(row) => run(row, "dismiss")}
        />
      ))}
    </span>
  );
});
