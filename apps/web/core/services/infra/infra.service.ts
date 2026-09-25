/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// api services
import { API_BASE_URL } from "@plane/constants";
import type {
  IInfraConnection,
  IInfraConnectionResources,
  IInfraConnectionVerifyResult,
  IInfraLink,
  IInfraLinkStatusEntry,
} from "@plane/types";
import { APIService } from "@/services/api.service";

export class InfraService extends APIService {
  constructor() {
    super(API_BASE_URL);
  }

  // ---------- workspace-scoped: connections ----------

  async fetchConnections(workspaceSlug: string): Promise<IInfraConnection[]> {
    return this.get(`/api/workspaces/${workspaceSlug}/infra/connections/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async createConnection(workspaceSlug: string, data: Partial<IInfraConnection> & { api_token?: string }): Promise<IInfraConnection> {
    return this.post(`/api/workspaces/${workspaceSlug}/infra/connections/`, data)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async updateConnection(workspaceSlug: string, connectionId: string, data: Partial<IInfraConnection> & { api_token?: string }): Promise<IInfraConnection> {
    return this.patch(`/api/workspaces/${workspaceSlug}/infra/connections/${connectionId}/`, data)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async deleteConnection(workspaceSlug: string, connectionId: string): Promise<void> {
    return this.delete(`/api/workspaces/${workspaceSlug}/infra/connections/${connectionId}/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async verifyConnection(workspaceSlug: string, connectionId: string): Promise<IInfraConnectionVerifyResult> {
    return this.post(`/api/workspaces/${workspaceSlug}/infra/connections/${connectionId}/verify/`, {})
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async fetchConnectionResources(workspaceSlug: string, connectionId: string, refresh = false): Promise<IInfraConnectionResources> {
    return this.get(`/api/workspaces/${workspaceSlug}/infra/connections/${connectionId}/resources/`, {
      params: refresh ? { refresh: "1" } : undefined,
    })
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  // ---------- project-scoped: links + status ----------

  async fetchLinks(workspaceSlug: string, projectId: string): Promise<IInfraLink[]> {
    return this.get(`/api/workspaces/${workspaceSlug}/projects/${projectId}/infra/links/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async createLink(workspaceSlug: string, projectId: string, data: Partial<IInfraLink>): Promise<IInfraLink> {
    return this.post(`/api/workspaces/${workspaceSlug}/projects/${projectId}/infra/links/`, data)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async updateLink(workspaceSlug: string, projectId: string, linkId: string, data: Partial<IInfraLink>): Promise<IInfraLink> {
    return this.patch(`/api/workspaces/${workspaceSlug}/projects/${projectId}/infra/links/${linkId}/`, data)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async deleteLink(workspaceSlug: string, projectId: string, linkId: string): Promise<void> {
    return this.delete(`/api/workspaces/${workspaceSlug}/projects/${projectId}/infra/links/${linkId}/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async fetchStatus(workspaceSlug: string, projectId: string, refresh = false): Promise<{ links: IInfraLinkStatusEntry[] }> {
    return this.get(`/api/workspaces/${workspaceSlug}/projects/${projectId}/infra/status/`, {
      params: refresh ? { refresh: "1" } : undefined,
    })
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }
}
