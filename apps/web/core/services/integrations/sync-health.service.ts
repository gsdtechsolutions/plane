/** Copyright (c) 2023-present Plane Software, Inc. and contributors. SPDX-License-Identifier: AGPL-3.0-only */
import { API_BASE_URL } from "@plane/constants";
import { APIService } from "@/services/api.service";

export type TSyncHealthGrade = "green" | "amber" | "red";
export type TSyncHealthSectionGrade = TSyncHealthGrade | "none";

export type TSyncHealthAsanaItem = {
  sync_id: string;
  project: { id: string; name: string };
  connection_name: string;
  workspace_gid_masked: string;
  direction: string;
  active: boolean;
  initial_sync_done: boolean;
  last_synced_at: string | null;
  cursor_age_minutes: number | null;
  last_log: { level: string; message: string; created_at: string | null } | null;
  errors_24h: number;
  warnings_24h: number;
  task_links: number;
  comment_links: number;
  health: TSyncHealthGrade;
};

export type TSyncHealthGithubItem = {
  mapping_id: string;
  repo: string;
  project: { id: string; name: string };
  account: string;
  active: boolean;
  sync_status: string;
  sync_error: string;
  last_delivery_at: string | null;
  delivery_errors_24h: number;
  automation_rules: number;
  open_prs_tracked: number;
  last_pr_at: string | null;
  health: TSyncHealthGrade;
};

export type TSyncHealthSlackItem = {
  mapping_id: string;
  channel: string;
  project: { id: string; name: string };
  team: string;
  active: boolean;
  sync_status: string;
  sync_error: string;
  last_synced_at: string | null;
  issue_links: number;
  messages_24h: number;
  health: TSyncHealthGrade;
};

export type TSyncHealthOverall = {
  asana: TSyncHealthSectionGrade;
  github: TSyncHealthSectionGrade;
  slack: TSyncHealthSectionGrade;
};

export type TSyncHealthPayload = {
  asana: TSyncHealthAsanaItem[];
  github: TSyncHealthGithubItem[];
  slack: TSyncHealthSlackItem[];
  overall: TSyncHealthOverall;
  computed_at: string;
  /** Present only when requested with explain=true. */
  explain?: string | null;
  explain_model?: string | null;
  explain_error?: string | null;
};

class SyncHealthService extends APIService {
  constructor() {
    super(API_BASE_URL);
  }

  /**
   * Sync-health / drift explainer: per-connection health grades for the
   * Asana, GitHub and Slack integrations. With explain=true the response also
   * carries an LLM plain-language drift report (or explain_error when AI is
   * not configured on the instance).
   */
  async getSyncHealth(workspaceSlug: string, explain?: boolean): Promise<TSyncHealthPayload> {
    return (
      await this.get(`/api/workspaces/${workspaceSlug}/integrations/sync-health/`, {
        params: explain ? { explain: "true" } : undefined,
      })
    ).data;
  }
}

export const syncHealthService = new SyncHealthService();
