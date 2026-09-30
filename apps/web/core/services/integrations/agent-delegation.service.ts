/** Copyright (c) 2023-present Plane Software, Inc. and contributors. SPDX-License-Identifier: AGPL-3.0-only */
import { API_BASE_URL } from "@plane/constants";
import { APIService } from "@/services/api.service";

export type DelegationStatus =
  | "queued"
  | "claimed"
  | "running"
  | "pr_opened"
  | "completed"
  | "failed"
  | "cancelled";

export type DelegationRun = {
  id: string;
  status: DelegationStatus;
  instructions: string;
  branch: string;
  pr_url: string;
  pr_number: number | null;
  result_excerpt: string;
  error: string;
  runner_id: string;
  created_at: string;
  claimed_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  issue: { id: string; display: string };
  created_by: { id: string; email: string } | null;
};

export type DelegationList = { count: number; results: DelegationRun[] };

class AgentDelegationService extends APIService {
  constructor() {
    super(API_BASE_URL);
  }
  private base(workspaceSlug: string, projectId: string, issueId: string) {
    return `/api/workspaces/${workspaceSlug}/projects/${projectId}/issues/${issueId}/agent-delegations/`;
  }
  async createDelegation(
    workspaceSlug: string,
    projectId: string,
    issueId: string,
    instructions?: string
  ): Promise<DelegationRun> {
    return (
      await this.post(this.base(workspaceSlug, projectId, issueId), instructions ? { instructions } : {})
    ).data;
  }
  async listDelegations(workspaceSlug: string, projectId: string, issueId: string): Promise<DelegationList> {
    return (await this.get(this.base(workspaceSlug, projectId, issueId))).data;
  }
  async getDelegation(
    workspaceSlug: string,
    projectId: string,
    issueId: string,
    runId: string
  ): Promise<DelegationRun> {
    return (await this.get(this.base(workspaceSlug, projectId, issueId) + `${runId}/`)).data;
  }
}

export const agentDelegationService = new AgentDelegationService();
