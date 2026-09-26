/** Copyright (c) 2023-present Plane Software, Inc. and contributors. SPDX-License-Identifier: AGPL-3.0-only */
import useSWR from "swr";
import { githubDeliveryService } from "@/services/integrations/github-delivery.service";
import type { GithubDevStatusCounts } from "@/services/integrations/github-delivery.service";

/**
 * Per-project GitHub development counts for every work item (pull request states + CI checks).
 * One shared SWR request per project — board and list cards all consume the same map.
 * Guests (403) resolve to an empty map so chips render nothing.
 */
export function useGithubDevStatus(workspaceSlug: string | undefined, projectId: string | undefined) {
  const { data, error } = useSWR(
    workspaceSlug && projectId ? ["github-dev-status", workspaceSlug, projectId] : null,
    () => githubDeliveryService.devStatus(workspaceSlug!, projectId!),
    { revalidateOnFocus: false, refreshInterval: 60000 }
  );
  const forbidden = (error as { response?: { status?: number } })?.response?.status === 403;
  return {
    counts: forbidden ? undefined : (data?.issues ?? {}),
    error: forbidden ? undefined : error,
  };
}

export function githubDevChipStyle(counts: GithubDevStatusCounts): { dot: string; text: string; label: string } {
  // A failing check queue outranks the pull request state: the work is not landing yet.
  if (counts.failing > 0) return { dot: "bg-secondary", text: "text-secondary", label: "checks failing" };
  if (counts.merged > 0) return { dot: "bg-custom-primary-200", text: "text-purple-500", label: "merged" };
  if (counts.closed > 0) return { dot: "bg-red-500", text: "text-red-500", label: "closed" };
  return { dot: "bg-green-500", text: "text-green-500", label: "open" };
}
