/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { observer } from "mobx-react";
// i18n
import { useTranslation } from "@plane/i18n";
// ui
import {
  CyclesOutline,
  DueDateOutline,
  EstimateOutline,
  LabelsOutline,
  MembersOutline,
  ModuleOutline,
  ParentOutline,
  PriorityOutline,
  StartDateOutline,
  StateOutline,
  UserOutline,
} from "@makeplane/propel/icons";
import { DateSelect } from "@plane/blocks/property-select";
import { cn, getDate, renderFormattedPayloadDate, shouldHighlightIssueDueDate } from "@plane/utils";
// components
import { EstimateSelect } from "@/components/dropdowns/estimate/estimate-select";
import { ButtonAvatars } from "@/components/dropdowns/member/avatar";
import { MemberSelect } from "@/components/dropdowns/member/member-select";
import { PrioritySelect } from "@/components/dropdowns/priority/priority-select";
import { StateSelect } from "@/components/dropdowns/state/state-select";
// hooks
import { useProjectEstimates } from "@/hooks/store/estimates";
import { useIssueDetail } from "@/hooks/store/use-issue-detail";
import { useMember } from "@/hooks/store/use-member";
import { useProject } from "@/hooks/store/use-project";
import { useProjectState } from "@/hooks/store/use-project-state";
import { useUser, useUserProfile } from "@/hooks/store/user";
// components
import { IssueParentSelectRoot } from "@/components/issues/parent-select-root";
import { IssueCustomProperties } from "@/components/issues/issue-detail-widgets/custom-properties/root";
import { SidebarPropertyListItem } from "@/components/common/layout/sidebar/property-list-item";
import { SidebarSectionCard } from "@/components/common/layout/sidebar/section-card";
import { IssueCycleSelect } from "./cycle-select";
import { IssueLabel } from "./label";
import { IssueModuleSelect } from "./module-select";
import type { TIssueOperations } from "./root";

type Props = {
  workspaceSlug: string;
  projectId: string;
  issueId: string;
  issueOperations: TIssueOperations;
  isEditable: boolean;
};

export const IssueDetailsSidebar = observer(function IssueDetailsSidebar(props: Props) {
  const { t } = useTranslation();
  const { workspaceSlug, projectId, issueId, issueOperations, isEditable } = props;
  // store hooks
  const { getProjectById } = useProject();
  const { areEstimateEnabledByProjectId } = useProjectEstimates();
  const {
    issue: { getIssueById },
  } = useIssueDetail();
  const { getUserDetails } = useMember();
  const { getStateById } = useProjectState();
  const { data: userProfile } = useUserProfile();
  const { data: currentUser } = useUser();
  const issue = getIssueById(issueId);
  if (!issue) return <></>;

  const createdByDetails = getUserDetails(issue.created_by);

  // derived values
  const projectDetails = getProjectById(issue.project_id);
  const stateDetails = getStateById(issue.state_id);

  const minDate = issue.start_date ? getDate(issue.start_date) : null;
  minDate?.setDate(minDate.getDate());

  const maxDate = issue.target_date ? getDate(issue.target_date) : null;
  maxDate?.setDate(maxDate.getDate());

  return (
    <>
      <div className="flex h-full w-full flex-col items-center overflow-hidden">
        <div className={`h-full w-full space-y-3 overflow-y-auto px-4 py-4 ${!isEditable ? "opacity-60" : ""}`}>
          <SidebarSectionCard label={t("common.details")}>
            <SidebarPropertyListItem icon={MembersOutline} label={t("common.assignee")} variant="stacked">
              <div className="flex flex-col items-start gap-1">
                <MemberSelect
                  testId="work-item-assignee-select"
                  value={(issue?.assignee_ids ?? [])[0]}
                  onChange={(val) => issueOperations.update(workspaceSlug, projectId, issueId, { assignee_ids: val ? [val] : [] })}
                  disabled={!isEditable}
                  projectId={projectId}
                  placeholder={t("issue.add.assignee_single")}
                  variant="select-ghost-md"
                  clearable
                  tooltip={{ heading: t("common.assignee") }}
                />
                {currentUser?.id && (issue?.assignee_ids ?? [])[0] !== currentUser.id && (
                  <button
                    type="button"
                    onClick={() =>
                      issueOperations.update(workspaceSlug, projectId, issueId, { assignee_ids: [currentUser.id as string] })
                    }
                    disabled={!isEditable}
                    className="text-11 font-medium text-accent-primary hover:text-accent-secondary hover:underline"
                  >
                    {t("power_k.contextual_actions.work_item.assign_to_me")}
                  </button>
                )}
              </div>
            </SidebarPropertyListItem>

            <SidebarPropertyListItem icon={PriorityOutline} label={t("common.priority")} variant="stacked">
              <PrioritySelect
                testId="work-item-priority-select"
                value={issue?.priority}
                onChange={(val) => issueOperations.update(workspaceSlug, projectId, issueId, { priority: val })}
                disabled={!isEditable}
                variant="select-ghost-md"
                tooltip
              />
            </SidebarPropertyListItem>

            <SidebarPropertyListItem icon={LabelsOutline} label={t("common.labels")} variant="stacked">
              <IssueLabel
                workspaceSlug={workspaceSlug}
                projectId={projectId}
                issueId={issueId}
                disabled={!isEditable}
              />
            </SidebarPropertyListItem>
          </SidebarSectionCard>

          <SidebarSectionCard label={t("common.planning")}>
            <SidebarPropertyListItem icon={ParentOutline} label={t("common.parent")} variant="stacked">
              <IssueParentSelectRoot
                className="h-7.5 w-full grow"
                workspaceSlug={workspaceSlug}
                projectId={projectId}
                issueId={issueId}
                issueOperations={issueOperations}
                disabled={!isEditable}
              />
            </SidebarPropertyListItem>

            {projectDetails?.cycle_view && (
              <SidebarPropertyListItem icon={CyclesOutline} label={t("common.cycle")} variant="stacked">
                <IssueCycleSelect
                  className="h-7.5 w-full grow"
                  workspaceSlug={workspaceSlug}
                  projectId={projectId}
                  issueId={issueId}
                  issueOperations={issueOperations}
                  disabled={!isEditable}
                />
              </SidebarPropertyListItem>
            )}

            {projectDetails?.module_view && (
              <SidebarPropertyListItem icon={ModuleOutline} label={t("common.modules")} variant="stacked">
                <IssueModuleSelect
                  className="w-full grow"
                  workspaceSlug={workspaceSlug}
                  projectId={projectId}
                  issueId={issueId}
                  issueOperations={issueOperations}
                  disabled={!isEditable}
                />
              </SidebarPropertyListItem>
            )}

            <SidebarPropertyListItem icon={StartDateOutline} label={t("common.order_by.start_date")} variant="stacked">
              <DateSelect
                testId="work-item-start-date-select"
                placeholder={t("issue.add.start_date")}
                value={getDate(issue.start_date) ?? null}
                onChange={(val) =>
                  issueOperations.update(workspaceSlug, projectId, issueId, {
                    start_date: val ? renderFormattedPayloadDate(val) : null,
                  })
                }
                maxDate={maxDate ?? undefined}
                disabled={!isEditable}
                clearable
                weekStartsOn={userProfile?.start_of_the_week}
                variant="select-ghost-md"
                showTooltip
                tooltipHeading={t("common.order_by.start_date")}
              />
            </SidebarPropertyListItem>

            <SidebarPropertyListItem icon={DueDateOutline} label={t("common.order_by.due_date")} variant="stacked">
              <DateSelect
                testId="work-item-due-date-select"
                placeholder={t("issue.add.due_date")}
                value={getDate(issue.target_date) ?? null}
                onChange={(val) =>
                  issueOperations.update(workspaceSlug, projectId, issueId, {
                    target_date: val ? renderFormattedPayloadDate(val) : null,
                  })
                }
                minDate={minDate ?? undefined}
                disabled={!isEditable}
                clearable
                className={cn({
                  "text-danger-primary": shouldHighlightIssueDueDate(issue.target_date, stateDetails?.group),
                })}
                weekStartsOn={userProfile?.start_of_the_week}
                variant="select-ghost-md"
                showTooltip
                tooltipHeading={t("common.order_by.due_date")}
              />
            </SidebarPropertyListItem>

            {projectId && areEstimateEnabledByProjectId(projectId) && (
              <SidebarPropertyListItem icon={EstimateOutline} label={t("common.estimate")} variant="stacked">
                <EstimateSelect
                  value={issue?.estimate_point ?? undefined}
                  onChange={(val: string | null) =>
                    issueOperations.update(workspaceSlug, projectId, issueId, { estimate_point: val })
                  }
                  projectId={projectId}
                  disabled={!isEditable}
                  variant="select-ghost-md"
                  placeholder={t("common.none")}
                  tooltip
                />
              </SidebarPropertyListItem>
            )}
          </SidebarSectionCard>

          {/* Fork feature: typed custom work-item properties */}
          <IssueCustomProperties
            workspaceSlug={workspaceSlug}
            projectId={projectId}
            issueId={issueId}
            disabled={!isEditable}
            asCard
          />

          {createdByDetails && (
            <div className="flex items-center gap-2 px-1 py-1 text-11 text-tertiary">
              <ButtonAvatars showTooltip userIds={createdByDetails.id} />
              <span className="truncate">{createdByDetails?.display_name}</span>
            </div>
          )}
        </div>
      </div>
    </>
  );
});
