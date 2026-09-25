/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// Fork feature: per-project board automation rules (WHEN trigger THEN actions).
import { API_BASE_URL } from "@plane/constants";
import { APIService } from "@/services/api.service";

export type TAutomationTriggerType = "state_changed" | "assignee_added";

export type TAutomationActionType =
  | "set_state"
  | "set_priority"
  | "add_label"
  | "remove_label"
  | "assign_member"
  | "set_due_date";

export interface IAutomationRuleAction {
  type: TAutomationActionType;
  value: string | null;
}

export interface IAutomationRule {
  id: string;
  name: string;
  trigger_type: TAutomationTriggerType;
  trigger_value: string | null;
  actions: IAutomationRuleAction[];
  is_active: boolean;
  project: string;
  workspace: string;
  created_at: string;
  updated_at: string;
}

export type PartialAutomationRule = Partial<IAutomationRule>;

export class AutomationRuleService extends APIService {
  constructor() {
    super(API_BASE_URL);
  }

  private rulesURL(workspaceSlug: string, projectId: string, ruleId?: string) {
    const base = `/api/workspaces/${workspaceSlug}/projects/${projectId}/automations/rules/`;
    return ruleId ? `${base}${ruleId}/` : base;
  }

  async listRules(workspaceSlug: string, projectId: string): Promise<IAutomationRule[]> {
    return this.get(this.rulesURL(workspaceSlug, projectId))
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async createRule(workspaceSlug: string, projectId: string, data: Partial<IAutomationRule>): Promise<IAutomationRule> {
    return this.post(this.rulesURL(workspaceSlug, projectId), data)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async updateRule(
    workspaceSlug: string,
    projectId: string,
    ruleId: string,
    data: Partial<IAutomationRule>
  ): Promise<IAutomationRule> {
    return this.patch(this.rulesURL(workspaceSlug, projectId, ruleId), data)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async deleteRule(workspaceSlug: string, projectId: string, ruleId: string): Promise<void> {
    return this.delete(this.rulesURL(workspaceSlug, projectId, ruleId))
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async toggleRule(workspaceSlug: string, projectId: string, ruleId: string): Promise<IAutomationRule> {
    return this.post(`${this.rulesURL(workspaceSlug, projectId, ruleId)}toggle/`, {})
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }
}
