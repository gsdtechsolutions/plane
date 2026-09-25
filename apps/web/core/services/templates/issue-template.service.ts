/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// Fork feature: per-project work item templates (list/CRUD + apply prefill).
import { API_BASE_URL } from "@plane/constants";
import { APIService } from "@/services/api.service";

export type TIssueTemplatePriority = "urgent" | "high" | "medium" | "low" | "none";

export interface IIssueTemplate {
  id: string;
  name: string;
  description_html: string;
  description_json: Record<string, unknown>;
  priority: TIssueTemplatePriority;
  state: string | null;
  labels: string[];
  assignees: string[];
  due_in_days: number | null;
  is_default: boolean;
  project: string;
  workspace: string;
  created_at: string;
  updated_at: string;
}

export type PartialIssueTemplate = Partial<IIssueTemplate>;

export interface IIssueTemplateApplyPayload {
  template_id: string;
  name: string;
  description_html: string;
  description_json: Record<string, unknown>;
  priority: TIssueTemplatePriority;
  state: string | null;
  label_ids: string[];
  assignee_ids: string[];
  target_date: string | null;
}

export class IssueTemplateService extends APIService {
  constructor() {
    super(API_BASE_URL);
  }

  private templatesURL(workspaceSlug: string, projectId: string, templateId?: string) {
    const base = `/api/workspaces/${workspaceSlug}/projects/${projectId}/issue-templates/`;
    return templateId ? `${base}${templateId}/` : base;
  }

  async listTemplates(workspaceSlug: string, projectId: string): Promise<IIssueTemplate[]> {
    return this.get(this.templatesURL(workspaceSlug, projectId))
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async createTemplate(
    workspaceSlug: string,
    projectId: string,
    data: PartialIssueTemplate
  ): Promise<IIssueTemplate> {
    return this.post(this.templatesURL(workspaceSlug, projectId), data)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async updateTemplate(
    workspaceSlug: string,
    projectId: string,
    templateId: string,
    data: PartialIssueTemplate
  ): Promise<IIssueTemplate> {
    return this.patch(this.templatesURL(workspaceSlug, projectId, templateId), data)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async deleteTemplate(workspaceSlug: string, projectId: string, templateId: string): Promise<void> {
    return this.delete(this.templatesURL(workspaceSlug, projectId, templateId))
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async applyTemplate(
    workspaceSlug: string,
    projectId: string,
    templateId: string
  ): Promise<IIssueTemplateApplyPayload> {
    return this.post(`${this.templatesURL(workspaceSlug, projectId, templateId)}apply/`, {})
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }
}
