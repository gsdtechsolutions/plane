/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// plane imports
import { API_BASE_URL } from "@plane/constants";
// services
import { APIService } from "@/services/api.service";

export interface ITimeEntry {
  id: string;
  project: string;
  workspace: string;
  issue: string;
  user: string;
  minutes: number;
  started_at: string | null;
  ended_at: string | null;
  description: string;
  created_at: string;
  updated_at: string;
}

export type TTimeEntryCreatePayload = {
  minutes: number;
  description?: string;
  started_at?: string | null;
  ended_at?: string | null;
};

export type TTimeEntryUpdatePayload = Partial<TTimeEntryCreatePayload>;

export interface IIssueTimeEntrySummary {
  total_minutes: number;
  entries_count: number;
  by_user: { user_id: string; display_name: string; minutes: number }[];
}

export type TWorkspaceTimeEntriesParams = {
  project_id?: string;
  issue_id?: string;
  start_date?: string;
  end_date?: string;
};

export class TimeEntryService extends APIService {
  constructor() {
    super(API_BASE_URL);
  }

  async listIssueTimeEntries(
    workspaceSlug: string,
    projectId: string,
    issueId: string
  ): Promise<ITimeEntry[]> {
    return this.get(`/api/workspaces/${workspaceSlug}/projects/${projectId}/issues/${issueId}/time-entries/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async createTimeEntry(
    workspaceSlug: string,
    projectId: string,
    issueId: string,
    payload: TTimeEntryCreatePayload
  ): Promise<ITimeEntry> {
    return this.post(`/api/workspaces/${workspaceSlug}/projects/${projectId}/issues/${issueId}/time-entries/`, payload)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async updateTimeEntry(
    workspaceSlug: string,
    projectId: string,
    issueId: string,
    entryId: string,
    payload: TTimeEntryUpdatePayload
  ): Promise<ITimeEntry> {
    return this.patch(
      `/api/workspaces/${workspaceSlug}/projects/${projectId}/issues/${issueId}/time-entries/${entryId}/`,
      payload
    )
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async deleteTimeEntry(workspaceSlug: string, projectId: string, issueId: string, entryId: string): Promise<void> {
    return this.delete(
      `/api/workspaces/${workspaceSlug}/projects/${projectId}/issues/${issueId}/time-entries/${entryId}/`
    )
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async issueTimeEntrySummary(
    workspaceSlug: string,
    projectId: string,
    issueId: string
  ): Promise<IIssueTimeEntrySummary> {
    return this.get(
      `/api/workspaces/${workspaceSlug}/projects/${projectId}/issues/${issueId}/time-entries/summary/`
    )
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async startTimer(workspaceSlug: string, projectId: string, issueId: string): Promise<ITimeEntry> {
    return this.post(`/api/workspaces/${workspaceSlug}/projects/${projectId}/issues/${issueId}/timer/start/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async stopTimer(
    workspaceSlug: string,
    projectId: string,
    issueId: string,
    payload?: { issue_id?: string }
  ): Promise<ITimeEntry> {
    return this.post(
      `/api/workspaces/${workspaceSlug}/projects/${projectId}/issues/${issueId}/timer/stop/`,
      payload ?? {}
    )
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async listWorkspaceTimeEntries(
    workspaceSlug: string,
    params?: TWorkspaceTimeEntriesParams
  ): Promise<{
    total_count: number;
    next_cursor: string | null;
    prev_cursor: string | null;
    next_page_results: boolean;
    prev_page_results: boolean;
    count: number;
    total_pages: number;
    total_results: number;
    extra_stats: unknown | null;
    results: ITimeEntry[];
  }> {
    return this.get(`/api/workspaces/${workspaceSlug}/time-entries/`, { params })
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }
}
