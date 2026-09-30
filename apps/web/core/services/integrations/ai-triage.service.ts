/** Copyright (c) 2023-present Plane Software, Inc. and contributors. SPDX-License-Identifier: AGPL-3.0-only */
import { API_BASE_URL } from "@plane/constants";
import { APIService } from "@/services/api.service";

export type SuggestionKind = "label" | "assignee" | "priority" | "summary";
export type SuggestionStatus = "pending" | "accepted" | "dismissed";

export type Suggestion = {
  id: string;
  kind: SuggestionKind;
  payload: Record<string, unknown>;
  confidence: number | null;
  status: SuggestionStatus;
  model: string | null;
  created_at: string;
  decided_at: string | null;
  decided_by: string | null;
};

/** HTTP statuses under which the chips silently hide: backend absent, no
 * access, or the instance has no LLM configured. */
export const AI_TRIAGE_HIDDEN_STATUSES = [403, 404, 503];

export function aiTriageHiddenStatus(error: unknown): number | undefined {
  const value = error as { response?: { status?: number } };
  return value?.response?.status;
}

class AITriageService extends APIService {
  constructor() {
    super(API_BASE_URL);
  }

  private suggestions(workspaceSlug: string, projectId: string, issueId: string) {
    return `/api/workspaces/${workspaceSlug}/projects/${projectId}/issues/${issueId}/triage-suggestions/`;
  }

  async getSuggestions(workspaceSlug: string, projectId: string, issueId: string): Promise<Suggestion[]> {
    const response = await this.get(this.suggestions(workspaceSlug, projectId, issueId));
    const data = response.data as { count: number; results: Suggestion[] };
    return data.results ?? [];
  }

  async acceptSuggestion(
    workspaceSlug: string,
    projectId: string,
    issueId: string,
    suggestionId: string
  ): Promise<Suggestion> {
    return (await this.post(this.suggestions(workspaceSlug, projectId, issueId) + `${suggestionId}/accept/`, {}))
      .data as Suggestion;
  }

  async dismissSuggestion(
    workspaceSlug: string,
    projectId: string,
    issueId: string,
    suggestionId: string
  ): Promise<Suggestion> {
    return (await this.post(this.suggestions(workspaceSlug, projectId, issueId) + `${suggestionId}/dismiss/`, {}))
      .data as Suggestion;
  }
}

export const aiTriageService = new AITriageService();
