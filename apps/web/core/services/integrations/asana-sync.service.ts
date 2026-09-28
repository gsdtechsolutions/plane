/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// plane imports
import { API_BASE_URL } from "@plane/constants";
// api services
import { APIService } from "@/services/api.service";

export interface TAsanaConnection {
  id: string;
  name: string;
  pat_preview: string;
  asana_workspace_gid: string;
  asana_workspace_name: string;
  is_active: boolean;
  last_verified_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface TAsanaRemoteProject {
  gid: string;
  name: string;
  archived: boolean;
}

export interface TAsanaRemoteSection {
  gid: string;
  name: string;
}

export interface TAsanaRemoteMember {
  gid: string;
  name: string;
}

export interface TAsanaProjectSync {
  id: string;
  connection: string;
  asana_project_gid: string;
  asana_project_name: string;
  direction: "pull" | "push" | "bidirectional";
  sync_subtasks: boolean;
  sync_comments: boolean;
  state_map: Record<string, { state_id?: string; name?: string }>;
  default_state_id: string | null;
  label_map: Record<string, string>;
  assignee_map: Record<string, string>;
  webhook_configured: boolean;
  initial_sync_done: boolean;
  last_synced_at: string | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface TAsanaSyncLog {
  id: string;
  direction: "pull" | "push";
  entity_type: string;
  entity_gid: string;
  issue: string | null;
  status: "success" | "error" | "skipped" | "conflict";
  message: string;
  detail: Record<string, unknown>;
  created_at: string;
}

export interface TAsanaWorkspaceSync {
  id: string;
  project_id: string;
  project_name: string;
  connection: string;
  asana_project_gid: string;
  asana_project_name: string;
  direction: TAsanaProjectSync["direction"];
  sync_subtasks: boolean;
  sync_comments: boolean;
  webhook_configured: boolean;
  initial_sync_done: boolean;
  last_synced_at: string | null;
  is_active: boolean;
}

export interface TAsanaAssigneeRow {
  gid: string;
  name: string;
  /** Current map value: member:<uuid> | label:<uuid> | auto | "" | null (unseen) */
  value: string | null;
}

export interface TAsanaPlaneMember {
  id: string;
  name: string;
  email: string;
}

export interface TAsanaAssigneeMapping {
  members: TAsanaAssigneeRow[];
  plane_members: TAsanaPlaneMember[];
  map: Record<string, string>;
}

export class AsanaSyncService extends APIService {
  constructor() {
    super(API_BASE_URL);
  }

  // --- workspace-level connections (admin only) --------------------------------

  async listConnections(workspaceSlug: string): Promise<TAsanaConnection[]> {
    return this.get(`/api/workspaces/${workspaceSlug}/asana-sync/connections/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response;
      });
  }

  async createConnection(
    workspaceSlug: string,
    data: { name: string; personal_access_token: string }
  ): Promise<TAsanaConnection> {
    return this.post(`/api/workspaces/${workspaceSlug}/asana-sync/connections/`, data)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response;
      });
  }

  async deleteConnection(workspaceSlug: string, connectionId: string): Promise<void> {
    return this.delete(`/api/workspaces/${workspaceSlug}/asana-sync/connections/${connectionId}/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response;
      });
  }

  async verifyConnection(workspaceSlug: string, connectionId: string): Promise<{
    verified: boolean;
    asana_user?: { gid: string; name: string };
    asana_workspace?: { gid: string; name: string };
    error?: string;
  }> {
    return this.post(`/api/workspaces/${workspaceSlug}/asana-sync/connections/${connectionId}/verify/`, {})
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response;
      });
  }

  async listRemoteProjects(workspaceSlug: string, connectionId: string, workspaceGid: string): Promise<{
    projects: TAsanaRemoteProject[];
  }> {
    return this.get(
      `/api/workspaces/${workspaceSlug}/asana-sync/connections/${connectionId}/remote/?resource=projects&workspace_gid=${workspaceGid}`
    )
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response;
      });
  }

  async listRemoteSections(workspaceSlug: string, connectionId: string, projectGid: string): Promise<{
    sections: TAsanaRemoteSection[];
  }> {
    return this.get(
      `/api/workspaces/${workspaceSlug}/asana-sync/connections/${connectionId}/remote/?resource=sections&project_gid=${projectGid}`
    )
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response;
      });
  }

  async listRemoteMembers(workspaceSlug: string, connectionId: string, projectGid: string): Promise<{
    members: TAsanaRemoteMember[];
  }> {
    return this.get(
      `/api/workspaces/${workspaceSlug}/asana-sync/connections/${connectionId}/remote/?resource=members&project_gid=${projectGid}`
    )
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response;
      });
  }

  // --- workspace-level sync management -----------------------------------------

  async listWorkspaceSyncs(workspaceSlug: string): Promise<TAsanaWorkspaceSync[]> {
    return this.get(`/api/workspaces/${workspaceSlug}/asana-sync/syncs/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response;
      });
  }

  async runWorkspaceSync(workspaceSlug: string, syncId: string): Promise<{ detail: string }> {
    return this.post(`/api/workspaces/${workspaceSlug}/asana-sync/syncs/${syncId}/run/`, {})
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response;
      });
  }

  async listWorkspaceSyncLogs(workspaceSlug: string, syncId: string): Promise<TAsanaSyncLog[]> {
    return this.get(`/api/workspaces/${workspaceSlug}/asana-sync/syncs/${syncId}/logs/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response;
      });
  }

  async getWorkspaceSyncAssignees(workspaceSlug: string, syncId: string): Promise<TAsanaAssigneeMapping> {
    return this.get(`/api/workspaces/${workspaceSlug}/asana-sync/syncs/${syncId}/assignees/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response;
      });
  }

  async saveWorkspaceSyncAssignees(
    workspaceSlug: string,
    syncId: string,
    assigneeMap: Record<string, string>
  ): Promise<TAsanaAssigneeMapping> {
    return this.put(`/api/workspaces/${workspaceSlug}/asana-sync/syncs/${syncId}/assignees/`, {
      assignee_map: assigneeMap,
    })
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response;
      });
  }

  async createWorkspaceProjectSync(
    workspaceSlug: string,
    projectId: string,
    data: {
      connection: string;
      asana_project_gid: string;
      asana_project_name?: string;
      direction: TAsanaProjectSync["direction"];
      default_state_id?: string | null;
    }
  ): Promise<TAsanaProjectSync> {
    return this.createProjectSync(workspaceSlug, projectId, data);
  }

  // --- project-level syncs (project admin only) ---------------------------------

  async listProjectSyncs(workspaceSlug: string, projectId: string): Promise<TAsanaProjectSync[]> {
    return this.get(`/api/workspaces/${workspaceSlug}/projects/${projectId}/asana-sync/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response;
      });
  }

  async createProjectSync(
    workspaceSlug: string,
    projectId: string,
    data: {
      connection: string;
      asana_project_gid: string;
      asana_project_name?: string;
      direction: TAsanaProjectSync["direction"];
      default_state_id?: string | null;
      sync_subtasks?: boolean;
      sync_comments?: boolean;
    }
  ): Promise<TAsanaProjectSync> {
    return this.post(`/api/workspaces/${workspaceSlug}/projects/${projectId}/asana-sync/`, data)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response;
      });
  }

  async updateProjectSync(
    workspaceSlug: string,
    projectId: string,
    syncId: string,
    data: Partial<Pick<TAsanaProjectSync, "direction" | "is_active" | "default_state_id" | "sync_subtasks" | "sync_comments">>
  ): Promise<TAsanaProjectSync> {
    return this.patch(`/api/workspaces/${workspaceSlug}/projects/${projectId}/asana-sync/${syncId}/`, data)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response;
      });
  }

  async deleteProjectSync(workspaceSlug: string, projectId: string, syncId: string): Promise<void> {
    return this.delete(`/api/workspaces/${workspaceSlug}/projects/${projectId}/asana-sync/${syncId}/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response;
      });
  }

  async setupWebhook(workspaceSlug: string, projectId: string, syncId: string): Promise<{
    webhook_gid: string;
    target: string;
    awaiting_handshake: boolean;
  }> {
    return this.post(`/api/workspaces/${workspaceSlug}/projects/${projectId}/asana-sync/${syncId}/webhook/`, {})
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response;
      });
  }

  async deleteWebhook(workspaceSlug: string, projectId: string, syncId: string): Promise<void> {
    return this.delete(`/api/workspaces/${workspaceSlug}/projects/${projectId}/asana-sync/${syncId}/webhook/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response;
      });
  }

  async runSyncNow(workspaceSlug: string, projectId: string, syncId: string): Promise<{ detail: string }> {
    return this.post(`/api/workspaces/${workspaceSlug}/projects/${projectId}/asana-sync/${syncId}/run/`, {})
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response;
      });
  }

  async listSyncLogs(workspaceSlug: string, projectId: string, syncId: string): Promise<TAsanaSyncLog[]> {
    return this.get(`/api/workspaces/${workspaceSlug}/projects/${projectId}/asana-sync/${syncId}/logs/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response;
      });
  }
}
