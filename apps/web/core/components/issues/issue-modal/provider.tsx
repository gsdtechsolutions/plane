/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import React, { useCallback, useState } from "react";
import { observer } from "mobx-react";
// plane imports
import { setToast } from "@plane/blocks/toast";
import { useTranslation } from "@plane/i18n";
import type { ISearchIssueResponse, TIssue } from "@plane/types";
// components
import { IssueModalContext } from "@/components/issues/issue-modal/context";
import type { THandleTemplateChangeProps } from "@/components/issues/issue-modal/context";
// services
import { IssueTemplateService } from "@/services/templates/issue-template.service";
// hooks
import { useUser } from "@/hooks/store/user/user-user";

export type TIssueModalProviderProps = {
  templateId?: string;
  dataForPreload?: Partial<TIssue>;
  allowedProjectIds?: string[];
  children: React.ReactNode;
};

export const IssueModalProvider = observer(function IssueModalProvider(props: TIssueModalProviderProps) {
  const { children, allowedProjectIds, templateId } = props;
  // states
  const [selectedParentIssue, setSelectedParentIssue] = useState<ISearchIssueResponse | null>(null);
  // Fork feature: work item templates — the create modal tracks the picked template id and
  // resolves it into a form prefill through the project's issue-templates apply endpoint.
  const [workItemTemplateId, setWorkItemTemplateId] = useState<string | null>(templateId ?? null);
  const [isApplyingTemplate, setIsApplyingTemplate] = useState(false);
  // store hooks
  const { projectsWithCreatePermissions } = useUser();
  const { t } = useTranslation();
  // derived values
  const projectIdsWithCreatePermissions = Object.keys(projectsWithCreatePermissions ?? {});

  const handleTemplateChange = useCallback(
    async ({ workspaceSlug, reset, editorRef }: THandleTemplateChangeProps) => {
      const currentTemplateId = workItemTemplateId;
      if (!currentTemplateId || !workspaceSlug) return;
      // Read the current form project via a no-op reset pass (sync, values unchanged).
      const projectIdRef: { current: string | null } = { current: null };
      reset((values) => {
        projectIdRef.current = values.project_id ?? null;
        return values;
      });
      const projectId = projectIdRef.current;
      if (!projectId) return;
      setIsApplyingTemplate(true);
      try {
        const payload = await new IssueTemplateService().applyTemplate(workspaceSlug, projectId, currentTemplateId);
        reset((values) => ({
          ...values,
          name: payload.name || values.name,
          description_html: payload.description_html ?? "<p></p>",
          priority: payload.priority,
          state_id: payload.state ?? values.state_id,
          label_ids: payload.label_ids ?? values.label_ids,
          assignee_ids: payload.assignee_ids ?? values.assignee_ids,
          target_date: payload.target_date ?? values.target_date ?? null,
        }));
        editorRef.current?.setEditorValue(payload.description_html ?? "<p></p>", true);
      } catch {
        setWorkItemTemplateId(null);
        setToast({
          type: "error",
          title: "Error!",
          message: t("project_settings.templates.picker.apply_error"),
        });
      } finally {
        setIsApplyingTemplate(false);
      }
    },
    [workItemTemplateId, t]
  );

  return (
    <IssueModalContext.Provider
      // oxlint-disable-next-line react/jsx-no-constructed-context-values
      value={{
        allowedProjectIds: allowedProjectIds ?? projectIdsWithCreatePermissions,
        workItemTemplateId,
        setWorkItemTemplateId,
        isApplyingTemplate,
        setIsApplyingTemplate,
        selectedParentIssue,
        setSelectedParentIssue,
        issuePropertyValues: {},
        setIssuePropertyValues: () => {},
        issuePropertyValueErrors: {},
        setIssuePropertyValueErrors: () => {},
        getIssueTypeIdOnProjectChange: () => null,
        getActiveAdditionalPropertiesLength: () => 0,
        handlePropertyValuesValidation: () => true,
        handleCreateUpdatePropertyValues: () => Promise.resolve(),
        handleProjectEntitiesFetch: () => Promise.resolve(),
        handleTemplateChange,
        handleConvert: () => Promise.resolve(),
        handleCreateSubWorkItem: () => Promise.resolve(),
      }}
    >
      {children}
    </IssueModalContext.Provider>
  );
});
