/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

/**
 * AFFiNE wiki sync types (fork feature).
 *
 * Mirrors the /api/workspaces/<slug>/affine/* payloads. The api_token is
 * write-only — the API never returns it.
 */

export type IAffineConflictStrategy = "newest_wins" | "affine_wins" | "plane_wins";
export type IAffineSyncDirectionSetting = "two_way" | "affine_to_plane" | "plane_to_affine";

export interface IAffineConnectionSettings {
  conflict_strategy?: IAffineConflictStrategy;
  direction?: IAffineSyncDirectionSetting;
  sync_plane_creates?: boolean;
  sync_deletions?: boolean;
}

export type IAffineSyncStatus = "never" | "ok" | "error";

export interface IAffineConnection {
  id: string;
  workspace: string;
  project: string;
  project_detail: {
    id: string;
    name: string;
    identifier: string;
  };
  affine_instance_url: string;
  affine_workspace_id: string;
  affine_workspace_name: string;
  settings: IAffineConnectionSettings;
  is_active: boolean;
  last_synced_at: string | null;
  last_sync_status: IAffineSyncStatus;
  last_sync_error: string;
  page_count: number;
  conflict_count: number;
  created_at: string;
  updated_at: string;
}

export interface IAffineWorkspaceSummary {
  id: string;
  name: string;
}

export type IAffinePageMapStatus = "pending" | "synced" | "conflict" | "error";
export type IAffinePageMapDirection = "pull" | "push" | "create" | "none";

export interface IAffinePageMap {
  id: string;
  page: string;
  page_detail: {
    id: string;
    name: string;
    archived_at: string | null;
    updated_at: string;
  } | null;
  affine_doc_id: string;
  affine_doc_title: string;
  affine_updated_at: string | null;
  last_synced_at: string | null;
  last_sync_direction: IAffinePageMapDirection;
  status: IAffinePageMapStatus;
  last_error: string;
  created_at: string;
  updated_at: string;
}

export interface IAffineSyncStats {
  pulled: number;
  pushed: number;
  created_plane: number;
  created_affine: number;
  conflicts: number;
  errors: number;
  noop: number;
  deleted_plane: number;
  deleted_affine: number;
  error_messages: string[];
}
