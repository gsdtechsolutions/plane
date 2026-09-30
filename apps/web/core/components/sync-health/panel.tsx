/** Copyright (c) 2023-present Plane Software, Inc. and contributors. SPDX-License-Identifier: AGPL-3.0-only */

import { useCallback, useState } from "react";
import useSWR from "swr";
import { ChevronDown, ChevronRight, Sparkles } from "lucide-react";
import {
  syncHealthService,
  type TSyncHealthAsanaItem,
  type TSyncHealthGithubItem,
  type TSyncHealthSlackItem,
} from "@/services/integrations/sync-health.service";

const DOT_COLORS: Record<string, string> = {
  green: "bg-green-500",
  amber: "bg-amber-500",
  red: "bg-red-500",
  none: "bg-custom-text-300/50",
};

type ExplainState = { status: "idle" | "loading" | "done" | "error"; text?: string; model?: string };

function HealthDot({ grade }: { grade: string }) {
  return (
    <span
      className={`inline-block size-2 shrink-0 rounded-full ${DOT_COLORS[grade] ?? DOT_COLORS.none}`}
      aria-hidden
    />
  );
}

function ageLabel(minutes: number | null, lastSyncedAt: string | null): string {
  const resolved = minutes ?? (lastSyncedAt ? Math.floor((Date.now() - new Date(lastSyncedAt).getTime()) / 60000) : null);
  if (resolved === null || Number.isNaN(resolved)) return "never synced";
  if (resolved < 1) return "synced just now";
  if (resolved < 60) return `synced ${resolved}m ago`;
  const hours = Math.floor(resolved / 60);
  if (hours < 48) return `synced ${hours}h ago`;
  return `synced ${Math.floor(hours / 24)}d ago`;
}

function ErrorBadge({ count }: { count: number }) {
  return (
    <span className="rounded bg-red-500/10 px-1.5 py-0.5 text-[11px] font-medium text-red-500">
      {count} {count === 1 ? "error" : "errors"} (24h)
    </span>
  );
}

type HealthRow = {
  id: string;
  name: string;
  health: string;
  cursor: string | null;
  errors: number | null;
  detail: string[];
};

function asanaRow(item: TSyncHealthAsanaItem): HealthRow {
  const detail = [
    `Plane project: ${item.project.name}`,
    `Connection: ${item.connection_name} (workspace ${item.workspace_gid_masked || "unknown"})`,
    `Direction: ${item.direction}${item.active ? "" : " (paused)"}`,
    `Links: ${item.task_links} ${item.task_links === 1 ? "task" : "tasks"}, ${item.comment_links} ${
      item.comment_links === 1 ? "comment" : "comments"
    }`,
  ];
  if (item.warnings_24h > 0) detail.push(`Warnings (24h): ${item.warnings_24h}`);
  if (item.last_log) {
    detail.push(
      `Last log [${item.last_log.level}] ${item.last_log.message || "—"}${
        item.last_log.created_at ? ` · ${new Date(item.last_log.created_at).toLocaleString()}` : ""
      }`,
    );
  }
  return {
    id: `asana-${item.sync_id}`,
    name: item.project.name,
    health: item.health,
    cursor: ageLabel(item.cursor_age_minutes, item.last_synced_at),
    errors: item.errors_24h,
    detail,
  };
}

function githubRow(item: TSyncHealthGithubItem): HealthRow {
  const detail = [
    `Plane project: ${item.project.name}`,
    `Account: ${item.account}${item.active ? "" : " (inactive)"}`,
    `Sync status: ${item.sync_status}${item.sync_error ? ` — ${item.sync_error}` : ""}`,
    `Open PRs tracked: ${item.open_prs_tracked}`,
    `Automation rules: ${item.automation_rules}`,
  ];
  if (item.last_pr_at) detail.push(`Last PR: ${new Date(item.last_pr_at).toLocaleString()}`);
  return {
    id: `github-${item.mapping_id}`,
    name: item.repo,
    health: item.health,
    cursor: item.last_delivery_at ? `Last delivery ${new Date(item.last_delivery_at).toLocaleString()}` : "never delivered",
    errors: item.delivery_errors_24h,
    detail,
  };
}

function slackRow(item: TSyncHealthSlackItem): HealthRow {
  const detail = [
    `Plane project: ${item.project.name}`,
    `Slack workspace: ${item.team}${item.active ? "" : " (inactive)"}`,
    `Sync status: ${item.sync_status}`,
    `Messages (24h): ${item.messages_24h}`,
    `Linked issues: ${item.issue_links}`,
  ];
  if (item.sync_error) detail.push(`Sync error: ${item.sync_error}`);
  return {
    id: `slack-${item.mapping_id}`,
    name: `#${item.channel}`,
    health: item.health,
    cursor: ageLabel(null, item.last_synced_at),
    errors: null,
    detail,
  };
}

function HealthCard({ row, expanded, onToggle }: { row: HealthRow; expanded: boolean; onToggle: () => void }) {
  return (
    <div className="rounded-md border border-subtle bg-surface-1 p-3">
      <button type="button" onClick={onToggle} className="flex w-full items-start gap-2 text-left">
        <HealthDot grade={row.health} />
        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-center gap-2 text-13 font-medium">
            <span className="break-words">{row.name}</span>
            {row.errors !== null && row.errors > 0 && <ErrorBadge count={row.errors} />}
          </span>
          {row.cursor && <span className="mt-0.5 block text-12 text-secondary">{row.cursor}</span>}
        </span>
        {expanded ? (
          <ChevronDown className="size-3.5 shrink-0 text-custom-text-300" aria-hidden />
        ) : (
          <ChevronRight className="size-3.5 shrink-0 text-custom-text-300" aria-hidden />
        )}
      </button>
      {expanded && (
        <div className="mt-2 space-y-1 border-t border-subtle pt-2">
          {row.detail.map((line, index) => (
            <p key={index} className="text-12 text-secondary">
              {line}
            </p>
          ))}
        </div>
      )}
    </div>
  );
}

function SkeletonCard() {
  return (
    <div className="animate-pulse rounded-md border border-subtle bg-surface-1 p-3">
      <div className="h-3 w-40 rounded bg-custom-background-80" />
      <div className="mt-2 h-2.5 w-24 rounded bg-custom-background-80" />
    </div>
  );
}

export function SyncHealthPanel({ workspaceSlug }: { workspaceSlug: string }) {
  const { data, error, isLoading } = useSWR(
    workspaceSlug ? ["sync-health", workspaceSlug] : null,
    () => syncHealthService.getSyncHealth(workspaceSlug),
    { revalidateOnFocus: false }
  );
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [explain, setExplain] = useState<ExplainState>({ status: "idle" });

  const handleExplain = useCallback(async () => {
    setExplain({ status: "loading" });
    try {
      const result = await syncHealthService.getSyncHealth(workspaceSlug, true);
      if (result.explain) {
        setExplain({ status: "done", text: result.explain, model: result.explain_model ?? undefined });
      } else {
        setExplain({ status: "error" });
      }
    } catch {
      setExplain({ status: "error" });
    }
  }, [workspaceSlug]);

  const toggle = (id: string) => setExpandedId((current) => (current === id ? null : id));

  const isEmpty = !!data && data.asana.length === 0 && data.github.length === 0 && data.slack.length === 0;
  const sections: { key: string; title: string; overall: string; rows: HealthRow[] }[] = data
    ? [
        { key: "asana", title: "Asana", overall: data.overall.asana, rows: data.asana.map(asanaRow) },
        { key: "github", title: "GitHub", overall: data.overall.github, rows: data.github.map(githubRow) },
        { key: "slack", title: "Slack", overall: data.overall.slack, rows: data.slack.map(slackRow) },
      ]
    : [];

  return (
    <section aria-labelledby="sync-health-heading" className="border-b border-subtle py-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 id="sync-health-heading" className="text-base font-medium">
            Sync health
          </h3>
          <p className="mt-0.5 text-12 text-secondary">
            {data
              ? `Overall: Asana ${data.overall.asana} · GitHub ${data.overall.github} · Slack ${data.overall.slack} · checked ${new Date(
                  data.computed_at
                ).toLocaleString()}`
              : "Grades each integration connection by sync freshness and recent errors."}
          </p>
        </div>
        {!isEmpty && (
          <button
            type="button"
            onClick={handleExplain}
            disabled={explain.status === "loading"}
            className="flex items-center gap-1.5 rounded-md border border-subtle px-3 py-1.5 text-xs font-medium hover:bg-custom-background-80 disabled:opacity-50"
          >
            <Sparkles className="size-3.5" aria-hidden />
            {explain.status === "loading" ? "Explaining..." : "Explain with AI"}
          </button>
        )}
      </div>

      {explain.status === "done" && explain.text && (
        <div className="mt-3 rounded-md border border-subtle bg-custom-background-90 p-4">
          <ul className="space-y-1 text-12">
            {explain.text
              .split("\n")
              .map((line) => line.trim())
              .filter(Boolean)
              .map((line, index) => (
                <li key={index} className="text-secondary">
                  {line.replace(/^[-•*\d.)\s]+/, "")}
                </li>
              ))}
          </ul>
          {explain.model && (
            <p className="mt-2 text-[11px] text-custom-text-300">Generated by {explain.model}</p>
          )}
        </div>
      )}
      {explain.status === "error" && (
        <p className="mt-3 text-12 text-custom-text-300">
          Configure AI (LLM_API_KEY in admin settings) to enable drift explanations
        </p>
      )}

      {error && !data && (
        <p className="mt-3 text-12 text-custom-text-300">Sync health could not be loaded. Please try again.</p>
      )}

      <div className="mt-4 space-y-5">
        {isLoading && !data ? (
          [0, 1, 2].map((index) => (
            <div key={index} className="space-y-2">
              <div className="h-3 w-24 animate-pulse rounded bg-custom-background-80" />
              <SkeletonCard />
            </div>
          ))
        ) : isEmpty ? (
          <p className="rounded-md border border-subtle bg-custom-background-90 p-4 text-12 text-custom-text-300">
            No integrations connected yet. Connect Asana, GitHub or Slack to see their sync health here.
          </p>
        ) : (
          sections.map((section) => (
            <div key={section.key}>
              <p className="flex items-center gap-2 text-13 font-medium">
                <HealthDot grade={section.overall} />
                {section.title}
              </p>
              <div className="mt-2 space-y-2">
                {section.rows.map((row) => (
                  <HealthCard key={row.id} row={row} expanded={expandedId === row.id} onToggle={() => toggle(row.id)} />
                ))}
              </div>
            </div>
          ))
        )}
      </div>
    </section>
  );
}
