/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { observer } from "mobx-react";
// plane imports
import { CircularProgress } from "@makeplane/propel/components/circular-progress";
import { useTranslation } from "@plane/i18n";
import type { TIssueServiceType } from "@plane/types";
import { EIssueServiceType } from "@plane/types";
// hooks
import { useIssueDetail } from "@/hooks/store/use-issue-detail";

type Props = {
  parentIssueId: string;
  issueServiceType?: TIssueServiceType;
};

export const SubIssuesCollapsibleTitle = observer(function SubIssuesCollapsibleTitle(props: Props) {
  const { parentIssueId, issueServiceType = EIssueServiceType.ISSUES } = props;
  // translation
  const { t } = useTranslation();
  // store hooks
  const {
    subIssues: { subIssuesByIssueId, stateDistributionByIssueId },
  } = useIssueDetail(issueServiceType);
  // derived values
  const subIssuesDistribution = stateDistributionByIssueId(parentIssueId);
  const subIssues = subIssuesByIssueId(parentIssueId);
  // Always render the section: before fetch and with zero children show a plain 0 (Jira-style Subtasks row).
  const totalCount = subIssues?.length ?? 0;
  const completedCount = totalCount ? (subIssuesDistribution?.completed?.length ?? 0) : 0;
  const percentage = completedCount && totalCount ? (completedCount / totalCount) * 100 : 0;

  return (
    <span className="inline-flex items-center gap-2">
      {issueServiceType === EIssueServiceType.EPICS ? t("issue.label", { count: 1 }) : t("common.sub_work_items")}
      {totalCount === 0 ? (
        <span className="text-13 text-tertiary">0</span>
      ) : (
        <span className="flex items-center gap-1.5 text-13 text-tertiary">
          <CircularProgress
            value={percentage}
            size="md"
            variant={percentage === 100 ? "success" : "brand"}
            aria-label="Sub-work-item progress"
          />
          <span>
            {completedCount}/{totalCount} {t("common.done")}
          </span>
        </span>
      )}
    </span>
  );
});
