/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useState } from "react";
import { observer } from "mobx-react";
import { useParams } from "next/navigation";
import { Trash2, Archive, Bell, BellOff } from "lucide-react";
// plane imports
import { setToast } from "@plane/blocks/toast";
import { Button } from "@makeplane/propel/components/button";
import { DateSelect } from "@plane/blocks/property-select";
import type { TBulkOperationsPayload, TIssue, TIssuePriorities } from "@plane/types";
import { cn, getDate, renderFormattedPayloadDate } from "@plane/utils";
// components
import { LabelSelect } from "@/components/dropdowns/label/label-select";
import { MemberSelect } from "@/components/dropdowns/member/member-select";
import { PrioritySelect } from "@/components/dropdowns/priority/priority-select";
import { StateSelect } from "@/components/dropdowns/state/state-select";
import { CycleSelect } from "@/components/dropdowns/cycle/cycle-select";
import { ModuleSelect } from "@/components/dropdowns/module/module-select";
// hooks
import { useIssuesStore } from "@/hooks/use-issue-layout-store";
import { useMultipleSelectStore } from "@/hooks/store/use-multiple-select-store";
import { useProjectState } from "@/hooks/store/use-project-state";
// local
import { BulkDeleteConfirmModal } from "./delete-modal";
import { BulkArchiveConfirmModal } from "./archive-modal";

type TPendingProperties = Partial<TBulkOperationsPayload["properties"]>;

type Props = {
  className?: string;
  wrapperClassName?: string;
  onClearSelection: () => void;
};

/**
 * @description Fork customization: floating bulk-operations action bar shown while multiple work
 * items are selected. Lets users apply state, priority, assignees, labels, cycle, modules, dates
 * and subscription to all selected work items at once, plus quick archive/delete actions. Backed
 * by the fork's `bulk-operation-issues` API endpoint.
 */
export const BulkOperationsActionBar = observer(function BulkOperationsActionBar(props: Props) {
  const { className, wrapperClassName, onClearSelection } = props;
  const { workspaceSlug, projectId } = useParams();
  const { selectedEntityIds, clearSelection } = useMultipleSelectStore();
  const {
    issues: { bulkUpdateProperties },
    issueMap,
  } = useIssuesStore();
  const { getStateById } = useProjectState();

  const [pending, setPending] = useState<TPendingProperties>({});
  const [isUpdating, setIsUpdating] = useState(false);

  const getCommonValue = <T,>(key: keyof TIssue): T | null => {
    if (selectedEntityIds.length === 0) return null;
    const firstIssue = issueMap[selectedEntityIds[0]];
    if (!firstIssue) return null;
    const firstVal = firstIssue[key];

    const isSame = selectedEntityIds.every((id) => {
      const issue = issueMap[id];
      return issue && issue[key] === firstVal;
    });

    return isSame ? (firstVal as T) : null;
  };

  const getCommonArrayValue = <T,>(key: keyof TIssue): T[] => {
    if (selectedEntityIds.length === 0) return [];
    const firstIssue = issueMap[selectedEntityIds[0]];
    if (!firstIssue) return [];
    const firstVal = (firstIssue[key] as T[]) || [];

    const isSame = selectedEntityIds.every((id) => {
      const issue = issueMap[id];
      if (!issue) return false;
      const val = (issue[key] as T[]) || [];
      if (val.length !== firstVal.length) return false;
      return val.every((v) => firstVal.includes(v));
    });

    return isSame ? firstVal : [];
  };

  const isMixedValue = (key: keyof TIssue): boolean => {
    if (selectedEntityIds.length <= 1) return false;
    const firstIssue = issueMap[selectedEntityIds[0]];
    if (!firstIssue) return false;
    const firstVal = firstIssue[key];

    if (Array.isArray(firstVal)) {
      const firstArray = firstVal as any[];
      return selectedEntityIds.some((id) => {
        const issue = issueMap[id];
        if (!issue) return true;
        const val = (issue[key] as any[]) || [];
        if (val.length !== firstArray.length) return true;
        return !val.every((v) => firstArray.includes(v));
      });
    }

    return selectedEntityIds.some((id) => {
      const issue = issueMap[id];
      return issue && issue[key] !== firstVal;
    });
  };

  const commonStateId = getCommonValue<string>("state_id");
  const commonPriority = getCommonValue<TIssuePriorities>("priority");
  const commonAssignees = getCommonArrayValue<string>("assignee_ids");
  const commonLabels = getCommonArrayValue<string>("label_ids");
  const commonCycle = getCommonValue<string>("cycle_id");
  const commonModules = getCommonArrayValue<string>("module_ids");
  const commonStartDate = getCommonValue<string>("start_date");
  const commonTargetDate = getCommonValue<string>("target_date");

  const isMixedState = isMixedValue("state_id");
  const isMixedPriority = isMixedValue("priority");
  const isMixedAssignees = isMixedValue("assignee_ids");
  const isMixedLabels = isMixedValue("label_ids");
  const isMixedCycle = isMixedValue("cycle_id");
  const isMixedModules = isMixedValue("module_ids");
  const isMixedStartDate = isMixedValue("start_date");
  const isMixedTargetDate = isMixedValue("target_date");

  const commonIsSubscribed = getCommonValue<boolean>("is_subscribed");
  const currentIsSubscribed =
    pending.is_subscribed !== undefined ? pending.is_subscribed : (commonIsSubscribed ?? false);
  const handleToggleSubscription = () => {
    updatePending({ is_subscribed: !currentIsSubscribed });
  };

  const [isDeleteOpen, setIsDeleteOpen] = useState(false);
  const [isArchiveOpen, setIsArchiveOpen] = useState(false);

  const selectedCount = selectedEntityIds.length;
  const projectIdStr = projectId?.toString() ?? undefined;
  const hasPendingChanges = Object.keys(pending).length > 0;

  const canArchive =
    selectedEntityIds.length > 0 &&
    selectedEntityIds.every((id) => {
      const issue = issueMap[id];
      if (!issue) return false;
      const state = getStateById(issue.state_id);
      return state?.group === "completed" || state?.group === "cancelled";
    });

  const updatePending = (patch: TPendingProperties) => setPending((prev) => ({ ...prev, ...patch }));

  const handleClear = () => {
    clearSelection();
    onClearSelection();
  };

  const handleUpdate = async () => {
    if (!workspaceSlug || !projectIdStr || !hasPendingChanges) return;
    setIsUpdating(true);
    try {
      await bulkUpdateProperties(workspaceSlug.toString(), projectIdStr, {
        issue_ids: selectedEntityIds,
        properties: pending,
      });
      setToast({
        type: "success",
        title: "Updated!",
        message: `${selectedCount} work item${selectedCount > 1 ? "s" : ""} updated successfully.`,
      });
      setPending({});
      clearSelection();
      onClearSelection();
    } catch {
      setToast({ type: "error", title: "Error", message: "Something went wrong. Please try again." });
    } finally {
      setIsUpdating(false);
    }
  };

  return (
    <>
      {/* Floating centered wide tray */}
      <div
        className={cn(
          "absolute bottom-4 left-1/2 z-[30] w-[calc(100%-4rem)] max-w-full -translate-x-1/2",
          wrapperClassName
        )}
      >
        <div
          className={cn(
            "flex items-center justify-between gap-3 rounded-lg border border-strong bg-surface-1 p-2 shadow-raised-200",
            className
          )}
        >
          {/* LEFT: Selection & Quick Actions */}
          <div className="flex shrink-0 items-center gap-2">
            <button
              onClick={handleClear}
              aria-label="Clear selection"
              className="flex items-center transition-opacity hover:opacity-80"
            >
              <span
                aria-hidden
                className="mr-1.5 flex size-3.5 shrink-0 items-center justify-center rounded-[2px] border border-strong bg-layer-1"
              >
                <span className="h-[1.5px] w-2 rounded-full bg-primary" />
              </span>
              <span className="text-caption-sm-regular font-semibold text-primary">{selectedCount} selected</span>
            </button>

            {/* Vertical Divider */}
            <div className="mx-1 h-5 border-r border-strong" />

            {/* Notification Toggle Action */}
            <button
              onClick={handleToggleSubscription}
              title={
                currentIsSubscribed ? "Mute notifications for selected" : "Subscribe to notifications for selected"
              }
              aria-label={currentIsSubscribed ? "Mute notifications for selected" : "Subscribe to notifications for selected"}
              className="flex h-7 w-7 items-center justify-center rounded text-secondary transition-colors hover:bg-layer-1 hover:text-primary"
            >
              {currentIsSubscribed ? <BellOff className="h-4 w-4" /> : <Bell className="h-4 w-4" />}
            </button>

            {/* Archive Action */}
            <button
              onClick={() => setIsArchiveOpen(true)}
              disabled={!canArchive}
              title={canArchive ? "Archive selected" : "Only completed or canceled work items can be archived"}
              aria-label="Archive selected"
              className="flex h-7 w-7 items-center justify-center rounded text-secondary transition-colors hover:bg-layer-1 hover:text-primary disabled:cursor-not-allowed disabled:opacity-40"
            >
              <Archive className="h-4 w-4" />
            </button>

            {/* Delete Action */}
            <button
              onClick={() => setIsDeleteOpen(true)}
              title="Delete selected"
              aria-label="Delete selected"
              className="flex h-7 w-7 items-center justify-center rounded text-secondary transition-colors hover:bg-layer-1 hover:text-primary"
            >
              <Trash2 className="h-4 w-4" />
            </button>
          </div>

          {/* MIDDLE: Property Dropdowns */}
          <div className="scrollbar-none flex flex-1 items-center gap-2 overflow-x-auto px-1">
            {/* State */}
            {projectIdStr && (
              <StateSelect
                projectId={projectIdStr}
                value={pending.state_id !== undefined ? pending.state_id : (commonStateId ?? null)}
                onChange={(val) => updatePending({ state_id: val })}
                variant="pill-sm"
                placeholder={isMixedState ? "State (Mixed)" : "State"}
              />
            )}

            {/* Priority */}
            <PrioritySelect
              value={pending.priority !== undefined ? pending.priority : (commonPriority ?? null)}
              onChange={(val) => updatePending({ priority: val })}
              variant="pill-sm"
              placeholder={isMixedPriority ? "Priority (Mixed)" : "Priority"}
            />

            {/* Assignees */}
            {projectIdStr && (
              <MemberSelect
                projectId={projectIdStr}
                value={pending.assignee_ids !== undefined ? pending.assignee_ids : commonAssignees}
                onChange={(val: string[]) => updatePending({ assignee_ids: val })}
                multiple
                variant="pill-sm"
                placeholder={isMixedAssignees ? "Assignees (Mixed)" : "Assignees"}
              />
            )}

            {/* Labels */}
            {projectIdStr && (
              <LabelSelect
                projectId={projectIdStr}
                value={pending.label_ids !== undefined ? pending.label_ids : commonLabels}
                onChange={(val) => updatePending({ label_ids: val })}
                variant="pill-sm"
                placeholder={isMixedLabels ? "Labels (Mixed)" : "Labels"}
              />
            )}

            {/* Cycle */}
            {projectIdStr && (
              <CycleSelect
                projectId={projectIdStr}
                value={pending.cycle_id !== undefined ? pending.cycle_id : (commonCycle ?? null)}
                onChange={(val) => updatePending({ cycle_id: val ?? undefined })}
                variant="pill-sm"
                placeholder={isMixedCycle ? "Cycle (Mixed)" : "Cycle"}
              />
            )}

            {/* Module */}
            {projectIdStr && (
              <ModuleSelect
                projectId={projectIdStr}
                value={(pending.module_ids !== undefined ? pending.module_ids : commonModules) ?? []}
                onChange={(val: string[]) => updatePending({ module_ids: val })}
                multiple
                variant="pill-sm"
                placeholder={isMixedModules ? "Modules (Mixed)" : "Modules"}
              />
            )}

            {/* Start date */}
            <DateSelect
              value={getDate(pending.start_date !== undefined ? pending.start_date : (commonStartDate ?? null)) ?? null}
              onChange={(val) => updatePending({ start_date: val ? renderFormattedPayloadDate(val) : null })}
              variant="pill-sm"
              placeholder={isMixedStartDate ? "Start date (Mixed)" : "Start date"}
              clearable
            />

            {/* Due date */}
            <DateSelect
              value={getDate(pending.target_date !== undefined ? pending.target_date : (commonTargetDate ?? null)) ?? null}
              onChange={(val) => updatePending({ target_date: val ? renderFormattedPayloadDate(val) : null })}
              variant="pill-sm"
              placeholder={isMixedTargetDate ? "Due date (Mixed)" : "Due date"}
              clearable
            />
          </div>

          {/* RIGHT: Update button */}
          <div className="flex shrink-0 items-center">
            <Button
              variant="primary"
              size="sm"
              stretch="auto"
              onClick={handleUpdate}
              disabled={!hasPendingChanges || isUpdating}
              loading={isUpdating}
              label="Update"
            />
          </div>
        </div>
      </div>

      <BulkDeleteConfirmModal
        isOpen={isDeleteOpen}
        issueIds={selectedEntityIds}
        onClose={() => setIsDeleteOpen(false)}
        onSuccess={() => {
          clearSelection();
          onClearSelection();
        }}
      />

      <BulkArchiveConfirmModal
        isOpen={isArchiveOpen}
        issueIds={selectedEntityIds}
        onClose={() => setIsArchiveOpen(false)}
        onSuccess={() => {
          clearSelection();
          onClearSelection();
        }}
      />
    </>
  );
});
