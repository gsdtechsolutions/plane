/** Copyright (c) 2023-present Plane Software, Inc. and contributors. SPDX-License-Identifier: AGPL-3.0-only */
import { API_BASE_URL } from "@plane/constants";
import { APIService } from "@/services/api.service";

export type DispatchStatus = "dispatching" | "active" | "completed" | "failed" | "cancelled";

/** Trimmed copy of one dispatcher event (web-origin transcript). */
export type DispatchEvent = {
  id: string;
  type: string;
  at: string;
  text?: string;
  reason?: string;
  branch?: string;
  summary?: string;
  harness?: string;
  model?: string;
  folder?: string;
  question_id?: string;
  options?: string[];
  commits?: string[];
  by?: string;
};

export type AgentDispatchRun = {
  id: string;
  origin: "web" | "slack";
  status: DispatchStatus;
  instructions: string;
  job_id: string;
  requester: string;
  preview: { app_url?: string; watch_url?: string; expires_at?: string };
  events: DispatchEvent[];
  created_at: string;
  updated_at: string;
};

class AgentDispatchService extends APIService {
  constructor() {
    super(API_BASE_URL);
  }
  private base(workspaceSlug: string, projectId: string, issueId: string) {
    return `/api/workspaces/${workspaceSlug}/projects/${projectId}/issues/${issueId}/agent-dispatch/`;
  }
  async createDispatch(
    workspaceSlug: string,
    projectId: string,
    issueId: string,
    instructions?: string
  ): Promise<AgentDispatchRun> {
    return (
      await this.post(this.base(workspaceSlug, projectId, issueId), instructions ? { instructions } : {})
    ).data;
  }
  async listDispatches(workspaceSlug: string, projectId: string, issueId: string): Promise<AgentDispatchRun[]> {
    return (await this.get(this.base(workspaceSlug, projectId, issueId))).data;
  }
  async sendMessage(
    workspaceSlug: string,
    projectId: string,
    issueId: string,
    runId: string,
    text: string
  ): Promise<AgentDispatchRun> {
    return (await this.post(this.base(workspaceSlug, projectId, issueId) + `${runId}/messages/`, { text })).data;
  }
  async cancelDispatch(
    workspaceSlug: string,
    projectId: string,
    issueId: string,
    runId: string
  ): Promise<AgentDispatchRun> {
    return (await this.post(this.base(workspaceSlug, projectId, issueId) + `${runId}/cancel/`)).data;
  }
}

export const agentDispatchService = new AgentDispatchService();
