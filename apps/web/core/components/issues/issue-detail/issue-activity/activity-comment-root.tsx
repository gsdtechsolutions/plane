/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { observer } from "mobx-react";
// plane imports
import type { E_SORT_ORDER, TActivityFilters, EActivityFilterType } from "@plane/constants";
import { BASE_ACTIVITY_FILTER_TYPES, filterActivityOnSelectedFilters } from "@plane/constants";
import type { TCommentsOperations } from "@plane/types";
// components
import { CommentCard } from "@/components/comments/card/root";
// hooks
import { useIssueDetail } from "@/hooks/store/use-issue-detail";
// local imports
import { IssueActivityItem } from "./activity/activity-list";
import { IssueActivityLoader } from "./loader";

type TIssueActivityCommentRoot = {
  workspaceSlug: string;
  projectId: string;
  isIntakeIssue: boolean;
  issueId: string;
  selectedFilters: TActivityFilters[];
  activityOperations: TCommentsOperations;
  showAccessSpecifier?: boolean;
  disabled?: boolean;
  sortOrder: E_SORT_ORDER;
  /** Jira-style History view: system activity grouped under day headers. */
  groupByDay?: boolean;
};

export const IssueActivityCommentRoot = observer(function IssueActivityCommentRoot(props: TIssueActivityCommentRoot) {
  const {
    workspaceSlug,
    isIntakeIssue,
    issueId,
    selectedFilters,
    activityOperations,
    showAccessSpecifier,
    projectId,
    disabled,
    sortOrder,
    groupByDay = false,
  } = props;
  // store hooks
  const {
    activity: { getActivityAndCommentsByIssueId, getActivityById },
    comment: { getCommentById },
  } = useIssueDetail();
  // derived values
  const activityAndComments = getActivityAndCommentsByIssueId(issueId, sortOrder);

  if (!activityAndComments) return <IssueActivityLoader />;

  if (activityAndComments.length <= 0) return null;

  const filteredActivityAndComments = filterActivityOnSelectedFilters(activityAndComments, selectedFilters);

  const renderItem = (activityComment: (typeof filteredActivityAndComments)[number], index: number) =>
    activityComment.activity_type === "COMMENT" ? (
      <CommentCard
        key={activityComment.id}
        workspaceSlug={workspaceSlug}
        entityId={issueId}
        comment={getCommentById(activityComment.id)}
        activityOperations={activityOperations}
        ends={index === 0 ? "top" : index === filteredActivityAndComments.length - 1 ? "bottom" : undefined}
        showAccessSpecifier={!!showAccessSpecifier}
        showCopyLinkOption={!isIntakeIssue}
        disabled={disabled}
        projectId={projectId}
        enableReplies
      />
    ) : BASE_ACTIVITY_FILTER_TYPES.includes(activityComment.activity_type as EActivityFilterType) ? (
      <IssueActivityItem
        key={activityComment.id}
        activityId={activityComment.id}
        ends={index === 0 ? "top" : index === filteredActivityAndComments.length - 1 ? "bottom" : undefined}
      />
    ) : null;

  if (groupByDay) {
    // Jira-style History: collapse consecutive items that share a day under one header.
    const dayLabel = (timestamp?: string | Date | null) => {
      if (!timestamp) return null;
      const date = new Date(timestamp);
      if (Number.isNaN(date.getTime())) return null;
      return `${date.toLocaleDateString("en-US", { weekday: "long" })}, ${date.toLocaleDateString("en-US", {
        month: "long",
        day: "numeric",
        year: "numeric",
      })}`;
    };

    const groups: { label: string | null; items: typeof filteredActivityAndComments }[] = [];
    filteredActivityAndComments.forEach((item) => {
      const ts = item.activity_type === "COMMENT" ? getCommentById(item.id)?.created_at : getActivityById(item.id)?.created_at;
      const label = dayLabel(ts);
      const last = groups[groups.length - 1];
      if (last && last.label === label) last.items.push(item);
      else groups.push({ label, items: [item] });
    });

    let runningIndex = 0;
    return (
      <div>
        {groups.map((group, groupIndex) => (
          <div key={`${group.label ?? "undated"}-${groupIndex}`} className="py-2">
            {group.label && (
              <div className="pb-2 text-body-xs-medium text-tertiary">{group.label}</div>
            )}
            {group.items.map((item) => {
              const node = renderItem(item, runningIndex);
              runningIndex += 1;
              return node;
            })}
          </div>
        ))}
      </div>
    );
  }

  return <div>{filteredActivityAndComments.map((activityComment, index) => renderItem(activityComment, index))}</div>
});
