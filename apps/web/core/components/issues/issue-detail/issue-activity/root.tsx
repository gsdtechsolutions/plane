/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useMemo } from "react";
import type { ReactNode } from "react";
import uniq from "lodash-es/uniq";
import { observer } from "mobx-react";
import { cn } from "@plane/utils";
// plane package imports
import type { TActivityFilters } from "@plane/constants";
import { E_SORT_ORDER, EActivityFilterType, defaultActivityFilters } from "@plane/constants";
import { useLocalStorage } from "@plane/hooks";
// i18n
import { useTranslation } from "@plane/i18n";
//types
import type { TFileSignedURLResponse, TIssueComment } from "@plane/types";
// components
import { CommentCreate } from "@/components/comments/comment-create";
// hooks
import { useProject } from "@/hooks/store/use-project";
// local imports
import { IssueActivityCommentRoot } from "./activity-comment-root";
import { useWorkItemCommentOperations } from "./helper";
import { ActivitySortRoot } from "./sort-root";

type TActivityTab = "all" | "comments" | "history" | "agents";

type TIssueActivity = {
  workspaceSlug: string;
  projectId: string;
  issueId: string;
  disabled?: boolean;
  isIntakeIssue?: boolean;
  /** Panel content for the Agents tab (delegation, AI suggestions). Tab renders only when provided. */
  agentsContent?: ReactNode;
};

export type TActivityOperations = {
  createComment: (data: Partial<TIssueComment>) => Promise<TIssueComment>;
  updateComment: (commentId: string, data: Partial<TIssueComment>) => Promise<void>;
  removeComment: (commentId: string) => Promise<void>;
  uploadCommentAsset: (blockId: string, file: File, commentId?: string) => Promise<TFileSignedURLResponse>;
};

const HISTORY_FILTERS: TActivityFilters[] = [
  EActivityFilterType.ACTIVITY,
  EActivityFilterType.STATE,
  EActivityFilterType.ASSIGNEE,
];

export const IssueActivity = observer(function IssueActivity(props: TIssueActivity) {
  const { workspaceSlug, projectId, issueId, disabled = false, isIntakeIssue = false, agentsContent } = props;
  // i18n
  const { t } = useTranslation();
  // tab state — Jira-style All / Comments / History / Agents
  const { storedValue: activityTab, setValue: setActivityTab } = useLocalStorage<TActivityTab>(
    "issue_activity_tab",
    "all"
  );
  const { setValue: setFilterValue, storedValue: selectedFilters } = useLocalStorage(
    "issue_activity_filters",
    defaultActivityFilters
  );
  const { setValue: setSortOrder, storedValue: sortOrder } = useLocalStorage("activity_sort_order", E_SORT_ORDER.ASC);

  const tab: TActivityTab = agentsContent ? (activityTab as TActivityTab) ?? "all" : activityTab === "agents" ? "all" : (activityTab as TActivityTab);

  // filters derived from the tab (kept in the same shape the feed filter expects)
  const tabFilters: TActivityFilters[] = useMemo(() => {
    if (tab === "comments") return [EActivityFilterType.COMMENT];
    if (tab === "history") return HISTORY_FILTERS;
    return defaultActivityFilters;
  }, [tab]);

  const { getProjectById } = useProject();

  const toggleSortOrder = () => {
    setSortOrder(sortOrder === E_SORT_ORDER.ASC ? E_SORT_ORDER.DESC : E_SORT_ORDER.ASC);
  };

  // helper hooks
  const activityOperations = useWorkItemCommentOperations(workspaceSlug, projectId, issueId);

  const project = getProjectById(projectId);
  const renderCommentCreationBox = useMemo(
    () => (
      <CommentCreate
        workspaceSlug={workspaceSlug}
        entityId={issueId}
        activityOperations={activityOperations}
        showToolbarInitially
        projectId={projectId}
      />
    ),
    [workspaceSlug, issueId, activityOperations, projectId]
  );
  if (!project) return <></>;

  // Labels are hardcoded English (fork convention, cf. the Development card) — the flat
  // common.* keys exist but i18next served stale cached namespaces for them in testing.
  const tabs: { key: TActivityTab; label: string }[] = [
    { key: "all", label: "All" },
    { key: "comments", label: "Comments" },
    { key: "history", label: "History" },
    ...(agentsContent ? [{ key: "agents" as TActivityTab, label: "Agents" }] : []),
  ];

  return (
    <div className="space-y-4">
      {/* header */}
      <div className="flex items-center justify-between">
        <div className="text-h5-medium text-primary">{t("common.activity")}</div>
        <div className="flex items-center gap-2">
          <ActivitySortRoot sortOrder={sortOrder || E_SORT_ORDER.ASC} toggleSort={toggleSortOrder} />
        </div>
      </div>

      {/* Jira-style tab strip */}
      <div className="flex items-center gap-1 border-b border-subtle">
        {tabs.map((item) => (
          <button
            key={item.key}
            type="button"
            onClick={() => {
              setActivityTab(item.key);
              // keep the legacy filter store in sync so other consumers see the same view
              setFilterValue(
                uniq(item.key === "comments" ? [EActivityFilterType.COMMENT] : item.key === "history" ? HISTORY_FILTERS : defaultActivityFilters)
              );
            }}
            className={cn(
              "-mb-px border-b-2 px-3 py-2 text-body-xs-medium transition-colors",
              tab === item.key
                ? "border-accent-primary text-primary"
                : "border-transparent text-tertiary hover:text-secondary"
            )}
          >
            {item.label}
          </button>
        ))}
      </div>

      {/* rendering activity */}
      <div className="space-y-3">
        <div className="min-h-[200px]">
          <div className="space-y-3">
            {!disabled && tab !== "agents" && sortOrder === E_SORT_ORDER.DESC && renderCommentCreationBox}
            {tab === "agents" ? (
              <div className="space-y-3">
                <p className="text-12 text-secondary">
                  Agent runs, delegations and AI suggestions for this work item will appear here.
                </p>
                {agentsContent}
              </div>
            ) : (
              <IssueActivityCommentRoot
                projectId={projectId}
                workspaceSlug={workspaceSlug}
                isIntakeIssue={isIntakeIssue}
                issueId={issueId}
                selectedFilters={tabFilters}
                activityOperations={activityOperations}
                showAccessSpecifier={!!project.anchor}
                disabled={disabled}
                sortOrder={sortOrder || E_SORT_ORDER.ASC}
                groupByDay={tab === "history"}
              />
            )}
            {!disabled && tab !== "agents" && sortOrder === E_SORT_ORDER.ASC && renderCommentCreationBox}
          </div>
        </div>
      </div>
    </div>
  );
});
