/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { observer } from "mobx-react";
// plane imports
import { useTranslation } from "@plane/i18n";
import { CalendarOutline } from "@makeplane/propel/icons";
import { DateSelect } from "@plane/blocks/property-select";
import { cn, getDate, renderFormattedPayloadDate, shouldHighlightIssueDueDate } from "@plane/utils";
// components
import { LabelSelect } from "@/components/dropdowns/label/label-select";
import { MemberSelect } from "@/components/dropdowns/member/member-select";
import { PrioritySelect } from "@/components/dropdowns/priority/priority-select";
import { StateSelect } from "@/components/dropdowns/state/state-select";
// hooks
import { useIssueDetail } from "@/hooks/store/use-issue-detail";
import { useProjectState } from "@/hooks/store/use-project-state";
import { useUserProfile } from "@/hooks/store/user";
// local imports
import type { TIssueOperations } from "./root";

type Props = {
  workspaceSlug: string;
  projectId: string;
  issueId: string;
  issueOperations: TIssueOperations;
  isEditable: boolean;
  isArchived: boolean;
  /** Hide the state chip — used on the full page where a hero status pill sits above the title. */
  hideState?: boolean;
};

/**
 * Quick-properties strip: the highest-traffic properties (state, priority, assignees,
 * due date, labels) rendered as pill chips directly under the title, so the ticket
 * reads at a glance without opening the right-hand properties panel.
 */
export const IssueMetaStrip = observer(function IssueMetaStrip(props: Props) {
  const { t } = useTranslation();
  const { workspaceSlug, projectId, issueId, issueOperations, isEditable, isArchived, hideState = false } = props;
  // store hooks
  const {
    issue: { getIssueById },
  } = useIssueDetail();
  const { getStateById } = useProjectState();
  const { data: userProfile } = useUserProfile();
  // derived values
  const issue = getIssueById(issueId);
  if (!issue) return null;
  const stateDetails = getStateById(issue.state_id);
  const disabled = isArchived || !isEditable;

  return (
    <div className={cn("flex flex-wrap items-center gap-2", disabled && "pointer-events-none opacity-70")}>
      {!hideState && (
        <StateSelect
          testId="work-item-state-select-strip"
          value={issue?.state_id}
          onChange={(val) => issueOperations.update(workspaceSlug, projectId, issueId, { state_id: val })}
          projectId={projectId}
          disabled={disabled}
          variant="pill-md"
          tooltip
        />
      )}

      <PrioritySelect
        testId="work-item-priority-select-strip"
        value={issue?.priority}
        onChange={(val) => issueOperations.update(workspaceSlug, projectId, issueId, { priority: val })}
        disabled={disabled}
        variant="pill-md"
        tooltip
      />

      <MemberSelect
        testId="work-item-assignee-select-strip"
        value={issue?.assignee_ids ?? []}
        onChange={(val) => issueOperations.update(workspaceSlug, projectId, issueId, { assignee_ids: val })}
        disabled={disabled}
        projectId={projectId}
        placeholder={t("issue.add.assignee")}
        multiple
        variant={(issue?.assignee_ids?.length ?? 0) > 0 ? "avatar-group-md" : "pill-md"}
        tooltip={{ heading: t("common.assignees") }}
      />

      <DateSelect
        testId="work-item-due-date-select-strip"
        placeholder={t("issue.add.due_date")}
        value={getDate(issue.target_date) ?? null}
        onChange={(val) =>
          issueOperations.update(workspaceSlug, projectId, issueId, {
            target_date: val ? renderFormattedPayloadDate(val) : null,
          })
        }
        disabled={disabled}
        clearable
        icon={<CalendarOutline className="size-3.5 shrink-0" />}
        className={cn({
          "text-danger-primary": shouldHighlightIssueDueDate(issue.target_date, stateDetails?.group),
        })}
        weekStartsOn={userProfile?.start_of_the_week}
        variant="pill-md"
        showTooltip
        tooltipHeading={t("common.order_by.due_date")}
      />

      <LabelSelect
        testId="work-item-label-select-strip"
        projectId={projectId}
        value={issue?.label_ids ?? []}
        onChange={(val) => issueOperations.update(workspaceSlug, projectId, issueId, { label_ids: val })}
        disabled={disabled}
        variant="pill-md"
        placeholder={t("common.add_label")}
        tooltip={{ heading: t("common.labels") }}
      />
    </div>
  );
});
