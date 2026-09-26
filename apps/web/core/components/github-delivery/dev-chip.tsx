/** Copyright (c) 2023-present Plane Software, Inc. and contributors. SPDX-License-Identifier: AGPL-3.0-only */
import { GitPullRequest } from "lucide-react";
import { githubDevChipStyle, useGithubDevStatus } from "@/hooks/use-github-dev-status";

/**
 * Compact board/list chip shown when a work item has GitHub activity.
 * Always visible when data exists — one shared dev-status fetch per project backs every chip.
 */
export function GithubDevChip({
  workspaceSlug,
  projectId,
  issueId,
}: {
  workspaceSlug: string | undefined;
  projectId: string | undefined;
  issueId: string;
}) {
  const { counts } = useGithubDevStatus(workspaceSlug, projectId);
  if (!counts) return null;
  const status = counts[issueId];
  if (!status) return null;
  const total = status.open + status.merged + status.closed;
  if (total === 0) return null;
  const style = githubDevChipStyle(status);
  return (
    <span
      title={`${total} pull request${total === 1 ? "" : "s"} · ${style.label}${
        status.pending > 0 ? ` · ${status.pending} check${status.pending === 1 ? "" : "s"} running` : ""
      }`}
      className="inline-flex shrink-0 items-center gap-1 rounded-full border border-subtle bg-surface-1 px-1.5 py-px text-10 text-secondary"
    >
      <GitPullRequest className={`size-2.5 ${style.text}`} aria-hidden />
      {total}
    </span>
  );
}
