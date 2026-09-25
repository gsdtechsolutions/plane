/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { observer } from "mobx-react";
// plane imports
import { useTranslation } from "@plane/i18n";
import { renderFormattedDate } from "@plane/utils";
import { DeleteOutline, PlayOutline, PlusOutline, StopOutline } from "@makeplane/propel/icons";
// services
import {
  TimeEntryService,
  type IIssueTimeEntrySummary,
  type ITimeEntry,
} from "@/services/timetracking";
// hooks
import { useMember } from "@/hooks/store/use-member";
import { useUser } from "@/hooks/store/user";

type Props = {
  workspaceSlug: string;
  projectId: string;
  issueId: string;
  disabled?: boolean;
};

const timeEntryService = new TimeEntryService();

/** 150 -> "2h 30m", 45 -> "45m". */
function formatMinutes(minutes: number): string {
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  if (h <= 0) return `${m}m`;
  return m > 0 ? `${h}h ${m}m` : `${h}h`;
}

/** Live duration of an open timer, computed client-side from started_at. */
function elapsedMinutes(startedAt: string, nowMs: number): number {
  const diffMs = nowMs - new Date(startedAt).getTime();
  if (!Number.isFinite(diffMs) || diffMs <= 0) return 0;
  return Math.floor(diffMs / 60000);
}

export function TimeTrackingCollapsible(props: Props) {
  const { workspaceSlug, projectId, issueId, disabled = false } = props;
  // states
  const [entries, setEntries] = useState<ITimeEntry[]>([]);
  const [summary, setSummary] = useState<IIssueTimeEntrySummary | undefined>(undefined);
  const [isLoading, setIsLoading] = useState(true);
  const [isAddFormOpen, setIsAddFormOpen] = useState(false);
  const [minutesInput, setMinutesInput] = useState("");
  const [descriptionInput, setDescriptionInput] = useState("");
  const [isMutating, setIsMutating] = useState(false);
  const [nowMs, setNowMs] = useState(() => Date.now());
  const isMounted = useRef(true);
  // translation
  const { t } = useTranslation();
  // store hooks
  const { data: currentUser } = useUser();
  const { getUserDetails } = useMember();

  const loadEntries = useCallback(async () => {
    try {
      const [entryList, issueSummary] = await Promise.all([
        timeEntryService.listIssueTimeEntries(workspaceSlug, projectId, issueId),
        timeEntryService.issueTimeEntrySummary(workspaceSlug, projectId, issueId),
      ]);
      if (!isMounted.current) return;
      setEntries(entryList);
      setSummary(issueSummary);
    } catch {
      // Endpoints 403 when the project flag is off — the widget is simply empty then.
    } finally {
      if (isMounted.current) setIsLoading(false);
    }
  }, [workspaceSlug, projectId, issueId]);

  useEffect(() => {
    isMounted.current = true;
    void loadEntries();
    return () => {
      isMounted.current = false;
    };
  }, [loadEntries]);

  // derived values
  const currentUserId = currentUser?.id;
  const runningEntry = useMemo(
    () =>
      entries.find(
        (entry) => entry.user === currentUserId && entry.started_at !== null && entry.ended_at === null
      ),
    [entries, currentUserId]
  );
  const totalMinutes = useMemo(
    () =>
      summary?.total_minutes ??
      entries.reduce((sum, entry) => (entry.ended_at === null ? sum : sum + (entry.minutes ?? 0)), 0),
    [summary, entries]
  );
  const displayNameByUserId = useMemo(() => {
    const map = new Map<string, string>();
    summary?.by_user.forEach((row) => map.set(row.user_id, row.display_name));
    return map;
  }, [summary]);

  const getDisplayName = useCallback(
    (userId: string) => displayNameByUserId.get(userId) || getUserDetails(userId)?.display_name || "",
    [displayNameByUserId, getUserDetails]
  );

  // keep the live elapsed readout ticking while a timer runs
  useEffect(() => {
    if (!runningEntry) return;
    setNowMs(Date.now());
    const timer = setInterval(() => setNowMs(Date.now()), 30000);
    return () => clearInterval(timer);
  }, [runningEntry]);

  // handlers
  const handleStartTimer = async () => {
    setIsMutating(true);
    try {
      await timeEntryService.startTimer(workspaceSlug, projectId, issueId);
      await loadEntries();
    } catch {
      // surface via empty state — server enforces single running timer
    } finally {
      setIsMutating(false);
    }
  };

  const handleStopTimer = async () => {
    setIsMutating(true);
    try {
      await timeEntryService.stopTimer(workspaceSlug, projectId, issueId);
      await loadEntries();
    } catch {
      // ignore — reload keeps UI consistent
    } finally {
      setIsMutating(false);
    }
  };

  const handleAddEntry = async () => {
    const minutes = Number(minutesInput);
    if (!Number.isFinite(minutes) || minutes < 0 || minutesInput.trim() === "") return;
    setIsMutating(true);
    try {
      await timeEntryService.createTimeEntry(workspaceSlug, projectId, issueId, {
        minutes: Math.round(minutes),
        description: descriptionInput.trim() || undefined,
      });
      if (!isMounted.current) return;
      setMinutesInput("");
      setDescriptionInput("");
      setIsAddFormOpen(false);
      await loadEntries();
    } catch {
      // keep the form open so the user can retry
    } finally {
      if (isMounted.current) setIsMutating(false);
    }
  };

  const handleDeleteEntry = async (entryId: string) => {
    setIsMutating(true);
    try {
      await timeEntryService.deleteTimeEntry(workspaceSlug, projectId, issueId, entryId);
      await loadEntries();
    } catch {
      // ignore
    } finally {
      setIsMutating(false);
    }
  };

  return (
    <div className="flex flex-col gap-2.5">
      <Collapsible
        defaultOpen={false}
        trigger={
          <span className="inline-flex items-center gap-2">
            {t("time_tracking")}
            <span className="text-14 leading-3! text-tertiary">
              {formatMinutes(totalMinutes)}
            </span>
            {runningEntry && (
              <span className="rounded-sm bg-green-500/10 px-1.5 py-0.5 text-11 font-medium text-green-500">
                {formatMinutes(elapsedMinutes(runningEntry.started_at as string, nowMs))} ·{" "}
                {t("time_tracking_timer_running")}
              </span>
            )}
          </span>
        }
        trailing={
          !disabled ? (
            <span className="inline-flex items-center gap-2">
              {runningEntry ? (
                <button
                  type="button"
                  onClick={handleStopTimer}
                  disabled={isMutating}
                  className="flex items-center gap-1 rounded-sm px-1.5 py-1 text-11 font-medium text-danger-primary hover:bg-red-500/10"
                  aria-label={t("time_tracking_stop_timer")}
                >
                  <StopOutline className="h-3.5 w-3.5" />
                  {t("time_tracking_stop_timer")}
                </button>
              ) : (
                <button
                  type="button"
                  onClick={handleStartTimer}
                  disabled={isMutating}
                  className="flex items-center gap-1 rounded-sm px-1.5 py-1 text-11 font-medium text-green-500 hover:bg-green-500/10"
                  aria-label={t("time_tracking_start_timer")}
                >
                  <PlayOutline className="h-3.5 w-3.5" />
                  {t("time_tracking_start_timer")}
                </button>
              )}
              {!isAddFormOpen && (
                <button
                  type="button"
                  onClick={() => setIsAddFormOpen(true)}
                  disabled={isMutating}
                  aria-label={t("time_tracking_log_time")}
                >
                  <PlusOutline className="h-4 w-4" />
                </button>
              )}
            </span>
          ) : undefined
        }
      >
        <div className="flex flex-col gap-3 px-6 py-2">
          {/* per-user breakdown */}
          {!!summary?.by_user?.length && (
            <div className="flex flex-col gap-1">
              <p className="text-11 font-medium text-tertiary">{t("time_tracking_by_user")}</p>
              <div className="flex flex-wrap items-center gap-2">
                {summary.by_user.map((row) => (
                  <span
                    key={row.user_id}
                    className="flex items-center gap-1 rounded-sm border border-subtle px-1.5 py-0.5 text-11 text-secondary"
                  >
                    {getDisplayName(row.user_id)}
                    <span className="text-tertiary">{formatMinutes(row.minutes)}</span>
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* inline add-entry form */}
          {isAddFormOpen && !disabled && (
            <div className="flex flex-col gap-2 rounded-md border border-subtle p-2">
              <div className="flex items-center gap-2">
                <input
                  type="number"
                  min={0}
                  step={1}
                  value={minutesInput}
                  onChange={(e) => setMinutesInput(e.target.value)}
                  placeholder={t("time_tracking_minutes")}
                  className="w-24 rounded-md border border-subtle bg-transparent px-2 py-1 text-sm text-secondary placeholder:text-placeholder focus:outline-none"
                />
                <input
                  type="text"
                  value={descriptionInput}
                  onChange={(e) => setDescriptionInput(e.target.value)}
                  placeholder={t("time_tracking_description_label")}
                  className="flex-1 rounded-md border border-subtle bg-transparent px-2 py-1 text-sm text-secondary placeholder:text-placeholder focus:outline-none"
                />
              </div>
              <div className="flex items-center justify-end gap-2">
                <button
                  type="button"
                  onClick={() => setIsAddFormOpen(false)}
                  className="rounded-sm px-2 py-1 text-11 font-medium text-tertiary hover:text-secondary"
                >
                  {t("time_tracking_cancel")}
                </button>
                <button
                  type="button"
                  onClick={handleAddEntry}
                  disabled={isMutating || minutesInput.trim() === ""}
                  className="rounded-sm bg-[#3f76ff] px-2 py-1 text-11 font-medium text-white disabled:opacity-50"
                >
                  {t("time_tracking_save")}
                </button>
              </div>
            </div>
          )}

          {/* entries list */}
          {isLoading ? (
            <p className="text-11 text-placeholder">{t("loading")}</p>
          ) : entries.length === 0 ? (
            <p className="text-11 text-placeholder">{t("time_tracking_no_entries")}</p>
          ) : (
            <ul className="flex flex-col">
              {entries.map((entry) => (
                <li
                  key={entry.id}
                  className="flex items-center justify-between gap-2 border-b border-subtle py-1.5 last:border-b-0"
                >
                  <span className="w-24 shrink-0 text-11 text-tertiary">
                    {renderFormattedDate(entry.created_at)}
                  </span>
                  <span className="w-16 shrink-0 text-11 font-medium text-secondary">
                    {entry.ended_at === null
                      ? `${formatMinutes(elapsedMinutes(entry.started_at as string, nowMs))} · ${t(
                          "time_tracking_timer_running"
                        )}`
                      : formatMinutes(entry.minutes)}
                  </span>
                  <span className="flex-1 truncate text-11 text-secondary">
                    {entry.description || "—"}
                  </span>
                  <span className="w-24 shrink-0 truncate text-right text-11 text-tertiary">
                    {getDisplayName(entry.user)}
                  </span>
                  {!disabled && entry.user === currentUserId && (
                    <button
                      type="button"
                      onClick={() => handleDeleteEntry(entry.id)}
                      disabled={isMutating}
                      className="shrink-0 text-tertiary hover:text-danger-primary"
                      aria-label={t("time_tracking_delete")}
                    >
                      <DeleteOutline className="h-3.5 w-3.5" />
                    </button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      </Collapsible>
    </div>
  );
}
