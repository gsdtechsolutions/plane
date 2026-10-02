/** Copyright (c) 2023-present Plane Software, Inc. and contributors. SPDX-License-Identifier: AGPL-3.0-only */
import useSWR from "swr";
import { Bot } from "lucide-react";
import { cn } from "@plane/utils";
import { agentDispatchService } from "@/services/integrations/agent-dispatch.service";
import type { AgentDispatchRun } from "@/services/integrations/agent-dispatch.service";

const STATUS_TONES: Record<string, string> = {
  dispatching: "border-subtle text-tertiary",
  active: "border-accent-subtle bg-accent-subtle font-semibold text-accent-primary",
  completed: "border-subtle bg-layer-2 text-secondary",
  failed: "border-subtle bg-danger-subtle text-danger-primary",
  cancelled: "border-subtle text-tertiary",
};

function StatusChip({ status }: { status: string }) {
  return (
    <span className={cn("inline-flex shrink-0 items-center rounded-full border px-1.5 py-0.5 text-11 leading-4", STATUS_TONES[status])}>
      {status}
    </span>
  );
}

/**
 * Agent-run evidence for the Development panel: the branch each dispatched
 * agent worked on and the commits it reported. Sourced from the dispatch
 * runs themselves, so local-only work (not yet on GitHub) shows up too.
 */
export function AgentRunsSection({ workspaceSlug, projectId, issueId }: { workspaceSlug: string; projectId: string; issueId: string }) {
  const { data } = useSWR(["agent-dispatch-development", workspaceSlug, projectId, issueId], () =>
    agentDispatchService.listDispatches(workspaceSlug, projectId, issueId),
    { refreshInterval: 20000 }
  );
  const runs = (data ?? []).filter((run: AgentDispatchRun) => run.branch || (run.commits && run.commits.length > 0));
  if (runs.length === 0) return null;
  return (
    <div className="py-1">
      <p className="text-11 font-medium tracking-wide text-tertiary uppercase">Agent runs</p>
      <ul>
        {runs.map((run) => (
          <li key={run.id} className="py-2">
            <div className="flex items-center gap-2">
              <Bot className="size-3.5 shrink-0 text-secondary" aria-hidden />
              <span className="min-w-0 flex-1 truncate text-13" title={run.branch ?? undefined}>
                {run.branch ?? "agent run"}
              </span>
              <StatusChip status={run.status} />
            </div>
            {(run.commits ?? []).map((commit) => (
              <p key={commit} className="ml-5 truncate py-0.5 text-12 text-tertiary" title={commit}>
                {commit}
              </p>
            ))}
          </li>
        ))}
      </ul>
    </div>
  );
}
