/** Copyright (c) 2023-present Plane Software, Inc. and contributors. SPDX-License-Identifier: AGPL-3.0-only */
import { API_BASE_URL } from "@plane/constants";
import { APIService } from "@/services/api.service";

export type AskReference = {
  id: string;
  name: string;
  display: string;
  state: string | null;
  state_group: string | null;
  priority: string | null;
  url: string;
};

export type AskResponse = {
  answer: string | null;
  model: string | null;
  references: AskReference[];
  message: string | null;
};

export type AskRequest = {
  question: string;
  project_id?: string;
};

/**
 * The instance has no LLM configured (LLM_API_KEY / LLM_MODEL missing in admin
 * settings). The backend answers 503 {"error": "ai_unconfigured"}.
 */
export function isAIUnconfigured(error: unknown): boolean {
  const value = error as { response?: { data?: { error?: unknown }; status?: number } };
  return value?.response?.status === 503 && value?.response?.data?.error === "ai_unconfigured";
}

export function askError(error: unknown): string {
  const value = error as { response?: { data?: { detail?: unknown } } };
  const detail = value?.response?.data?.detail;
  if (typeof detail === "string") return detail;
  return "The question could not be answered. Please try again.";
}

class WorkspaceQAService extends APIService {
  constructor() {
    super(API_BASE_URL);
  }

  async ask(workspaceSlug: string, data: AskRequest): Promise<AskResponse> {
    return (await this.post(`/api/workspaces/${workspaceSlug}/ask/`, data)).data;
  }
}

export const workspaceQAService = new WorkspaceQAService();
