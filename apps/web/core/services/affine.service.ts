/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// api services
import { API_BASE_URL } from "@plane/constants";
import type { IAffineConnection, IAffinePageMap, IAffineWorkspaceSummary, IAffineSyncStats } from "@plane/types";
import { APIService } from "@/services/api.service";

export class AffineService extends APIService {
  constructor() {
    super(API_BASE_URL);
  }

  /**
   * Validate an AFFiNE token and list reachable workspaces (pre-connect probe).
   */
  async probeWorkspaces(
    workspaceSlug: string,
    payload: { instance_url: string; api_token: string }
  ): Promise<{ workspaces: IAffineWorkspaceSummary[] }> {
    return this.post(`/api/workspaces/${workspaceSlug}/affine/probe/`, payload)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async fetchConnection(workspaceSlug: string): Promise<IAffineConnection> {
    return this.get(`/api/workspaces/${workspaceSlug}/affine/connection/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async createConnection(
    workspaceSlug: string,
    payload: Partial<IAffineConnection> & { api_token: string }
  ): Promise<IAffineConnection> {
    return this.post(`/api/workspaces/${workspaceSlug}/affine/connection/`, payload)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async updateConnection(workspaceSlug: string, payload: Partial<IAffineConnection> & { api_token?: string }): Promise<IAffineConnection> {
    return this.patch(`/api/workspaces/${workspaceSlug}/affine/connection/`, payload)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async deleteConnection(workspaceSlug: string): Promise<void> {
    return this.delete(`/api/workspaces/${workspaceSlug}/affine/connection/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async triggerSync(
    workspaceSlug: string
  ): Promise<{ queued: boolean; stats?: IAffineSyncStats; page_count?: number }> {
    return this.post(`/api/workspaces/${workspaceSlug}/affine/connection/sync/`, {})
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async fetchPageMaps(workspaceSlug: string): Promise<IAffinePageMap[]> {
    return this.get(`/api/workspaces/${workspaceSlug}/affine/connection/pages/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }
}
