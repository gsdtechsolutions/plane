/** Copyright (c) 2023-present Plane Software, Inc. and contributors. SPDX-License-Identifier: AGPL-3.0-only */
import { API_BASE_URL } from "@plane/constants";
import { APIService } from "@/services/api.service";

export type TLinkedActivitySource = "github" | "asana" | "slack";

export type TLinkedActivityEvent = {
  id: string;
  source: TLinkedActivitySource;
  type: string;
  title: string;
  url: string | null;
  actor: string | null;
  timestamp: string | null;
  meta: Record<string, unknown>;
};

export type TLinkedActivityResponse = {
  count: number;
  offset: number;
  limit: number;
  results: TLinkedActivityEvent[];
};

export type TLinkedActivityParams = {
  source?: TLinkedActivitySource;
  limit?: number;
  offset?: number;
};

class LinkedActivityService extends APIService {
  constructor() {
    super(API_BASE_URL);
  }

  async getLinkedActivity(
    workspaceSlug: string,
    projectId: string,
    issueId: string,
    params?: TLinkedActivityParams
  ): Promise<TLinkedActivityResponse> {
    const search = new URLSearchParams();
    if (params?.source) search.set("source", params.source);
    if (params?.limit != null) search.set("limit", String(params.limit));
    if (params?.offset != null) search.set("offset", String(params.offset));
    const query = search.toString();
    return (
      await this.get(
        `/api/workspaces/${workspaceSlug}/projects/${projectId}/issues/${issueId}/linked-activity/${
          query ? `?${query}` : ""
        }`
      )
    ).data;
  }
}

export const linkedActivityService = new LinkedActivityService();
