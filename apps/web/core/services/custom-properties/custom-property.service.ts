/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// Fork feature: per-project typed custom work-item properties.

import { API_BASE_URL } from "@plane/constants";
import { APIService } from "@/services/api.service";

export type TCustomPropertyType = "text" | "number" | "date" | "select" | "checkbox";

export interface ICustomPropertyOption {
  id: string;
  name: string;
  color: string;
}

export interface ICustomPropertySettings {
  required?: boolean;
  description?: string;
  unit?: string;
  options?: ICustomPropertyOption[];
}

export interface ICustomProperty {
  id: string;
  name: string;
  type: TCustomPropertyType;
  settings_json: ICustomPropertySettings;
  sort_order: number;
  is_active: boolean;
  project: string;
  workspace: string;
  created_at: string;
  updated_at: string;
}

// Typed client-facing value per property type (null = unanswered).
// select is always an array of option ids ([] = unanswered).
export type TCustomPropertyValue = string | number | boolean | string[] | null;

export interface ICustomPropertyValuesResponse {
  properties: ICustomProperty[];
  property_values: Record<string, TCustomPropertyValue>;
}

export class CustomPropertyService extends APIService {
  constructor() {
    super(API_BASE_URL);
  }

  private propertiesURL(workspaceSlug: string, projectId: string, propertyId?: string) {
    const base = `/api/workspaces/${workspaceSlug}/projects/${projectId}/custom-properties/`;
    return propertyId ? `${base}${propertyId}/` : base;
  }

  private issueValuesURL(workspaceSlug: string, projectId: string, issueId: string) {
    return `/api/workspaces/${workspaceSlug}/projects/${projectId}/issues/${issueId}/custom-property-values/`;
  }

  async listProperties(workspaceSlug: string, projectId: string): Promise<ICustomProperty[]> {
    return this.get(this.propertiesURL(workspaceSlug, projectId))
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async createProperty(
    workspaceSlug: string,
    projectId: string,
    data: Partial<ICustomProperty>
  ): Promise<ICustomProperty> {
    return this.post(this.propertiesURL(workspaceSlug, projectId), data)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async updateProperty(
    workspaceSlug: string,
    projectId: string,
    propertyId: string,
    data: Partial<ICustomProperty>
  ): Promise<ICustomProperty> {
    return this.patch(this.propertiesURL(workspaceSlug, projectId, propertyId), data)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async deleteProperty(workspaceSlug: string, projectId: string, propertyId: string): Promise<void> {
    return this.delete(this.propertiesURL(workspaceSlug, projectId, propertyId))
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async reorderProperties(
    workspaceSlug: string,
    projectId: string,
    orderedIds: string[]
  ): Promise<ICustomProperty[]> {
    return this.post(`${this.propertiesURL(workspaceSlug, projectId)}reorder/`, { ordered_ids: orderedIds })
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async getIssueValues(
    workspaceSlug: string,
    projectId: string,
    issueId: string
  ): Promise<ICustomPropertyValuesResponse> {
    return this.get(this.issueValuesURL(workspaceSlug, projectId, issueId))
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async putIssueValues(
    workspaceSlug: string,
    projectId: string,
    issueId: string,
    values: Record<string, TCustomPropertyValue>
  ): Promise<ICustomPropertyValuesResponse> {
    return this.put(this.issueValuesURL(workspaceSlug, projectId, issueId), { property_values: values })
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }
}
