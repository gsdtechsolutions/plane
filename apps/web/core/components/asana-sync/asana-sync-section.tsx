/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useCallback, useMemo, useState } from "react";
import useSWR from "swr";
import { observer } from "mobx-react";
// plane imports
import { useTranslation } from "@plane/i18n";
import { setToast } from "@plane/blocks/toast";
// services
import { ProjectService } from "@/services/project";
import { ProjectStateService } from "@/services/project/project-state.service";
import {
  AsanaSyncService,
  type TAsanaConnection,
  type TAsanaRemoteMember,
  type TAsanaRemoteProject,
  type TAsanaRemoteSection,
  type TAsanaSyncLog,
  type TAsanaWorkspaceSync,
} from "@/services/integrations/asana-sync.service";

const asanaSyncService = new AsanaSyncService();
const projectService = new ProjectService();
const projectStateService = new ProjectStateService();

type TDirection = "pull" | "push" | "bidirectional";

function StatusPill({ status }: { status: TAsanaSyncLog["status"] }) {
  const tone =
    status === "success"
      ? "bg-green-500/10 text-green-600"
      : status === "error"
        ? "bg-red-500/10 text-red-500"
        : status === "conflict"
          ? "bg-amber-500/10 text-amber-600"
          : "bg-custom-text-300/10 text-custom-text-300";
  return <span className={`rounded px-1.5 py-0.5 text-[11px] font-medium ${tone}`}>{status}</span>;
}

function AsanaSyncSectionBase({ workspaceSlug }: { workspaceSlug: string }) {
  const { t } = useTranslation();

  const connectionFetcher = useCallback(
    () => asanaSyncService.listConnections(workspaceSlug).catch(() => [] as TAsanaConnection[]),
    [workspaceSlug]
  );
  const { data: connections, mutate: mutateConnections } = useSWR(
    workspaceSlug ? `ASANA_CONNECTIONS_${workspaceSlug}` : null,
    connectionFetcher
  );
  const activeConnection = connections?.[0];

  // --- connect form state
  const [name, setName] = useState("Asana");
  const [pat, setPat] = useState("");
  const [creating, setCreating] = useState(false);
  const [verifying, setVerifying] = useState(false);

  // --- mapping form state
  const { data: projects } = useSWR(
    activeConnection ? `ASANA_PLANE_PROJECTS_${workspaceSlug}` : null,
    () => projectService.getProjectsLite(workspaceSlug)
  );
  const { data: remoteProjects } = useSWR(
    activeConnection?.asana_workspace_gid ? `ASANA_REMOTE_PROJECTS_${activeConnection.id}` : null,
    () =>
      asanaSyncService
        .listRemoteProjects(workspaceSlug, activeConnection!.id, activeConnection!.asana_workspace_gid)
        .then((r) => r.projects)
        .catch(() => [] as TAsanaRemoteProject[])
  );
  const [planeProjectId, setPlaneProjectId] = useState("");
  const [asanaProjectGid, setAsanaProjectGid] = useState("");
  const [direction, setDirection] = useState<TDirection>("bidirectional");
  const [defaultStateId, setDefaultStateId] = useState("");
  const [mapping, setMapping] = useState(false);

  const { data: projectStates } = useSWR(
    planeProjectId ? `ASANA_STATES_${workspaceSlug}_${planeProjectId}` : null,
    () => projectStateService.getStates(workspaceSlug, planeProjectId)
  );

  const syncsFetcher = useCallback(
    () => asanaSyncService.listWorkspaceSyncs(workspaceSlug).catch(() => [] as TAsanaWorkspaceSync[]),
    [workspaceSlug]
  );
  const { data: syncs, mutate: mutateSyncs } = useSWR(
    workspaceSlug ? `ASANA_SYNCS_${workspaceSlug}` : null,
    syncsFetcher
  );
  const [logsFor, setLogsFor] = useState<string | null>(null);
  const [logs, setLogs] = useState<TAsanaSyncLog[]>([]);
  const [runningId, setRunningId] = useState<string | null>(null);

  const selectedRemoteProject = useMemo(
    () => remoteProjects?.find((p) => p.gid === asanaProjectGid),
    [remoteProjects, asanaProjectGid]
  );

  const toast = useCallback(
    (type: "success" | "error", titleKey: string, message = "") =>
      setToast({ type, title: t(titleKey), message }),
    [t]
  );

  const handleConnect = useCallback(async () => {
    if (!pat.trim()) return;
    setCreating(true);
    try {
      await asanaSyncService.createConnection(workspaceSlug, {
        name: name.trim() || "Asana",
        personal_access_token: pat.trim(),
      });
      setPat("");
      await mutateConnections();
      toast("success", "asana_sync.toasts.connected_title");
    } catch {
      toast("error", "asana_sync.toasts.error_title");
    } finally {
      setCreating(false);
    }
  }, [workspaceSlug, name, pat, mutateConnections, toast]);

  const handleVerify = useCallback(async () => {
    if (!activeConnection) return;
    setVerifying(true);
    try {
      const result = await asanaSyncService.verifyConnection(workspaceSlug, activeConnection.id);
      if (result.verified) {
        toast("success", "asana_sync.toasts.verified_title", `${result.asana_workspace?.name ?? ""} · ${result.asana_user?.name ?? ""}`);
      } else {
        toast("error", "asana_sync.toasts.error_title", result.error ?? "");
      }
      await mutateConnections();
    } catch {
      toast("error", "asana_sync.toasts.error_title");
    } finally {
      setVerifying(false);
    }
  }, [workspaceSlug, activeConnection, mutateConnections, toast]);

  const handleDisconnect = useCallback(async () => {
    if (!activeConnection) return;
    try {
      await asanaSyncService.deleteConnection(workspaceSlug, activeConnection.id);
      await mutateConnections();
      await mutateSyncs();
    } catch {
      toast("error", "asana_sync.toasts.error_title");
    }
  }, [workspaceSlug, activeConnection, mutateConnections, mutateSyncs, toast]);

  const handleMap = useCallback(async () => {
    if (!activeConnection || !planeProjectId || !asanaProjectGid) return;
    setMapping(true);
    try {
      await asanaSyncService.createWorkspaceProjectSync(workspaceSlug, planeProjectId, {
        connection: activeConnection.id,
        asana_project_gid: asanaProjectGid,
        asana_project_name: selectedRemoteProject?.name ?? "",
        direction,
        default_state_id: defaultStateId || null,
      });
      setPlaneProjectId("");
      setAsanaProjectGid("");
      setDefaultStateId("");
      await mutateSyncs();
      toast("success", "asana_sync.toasts.mapped_title");
    } catch {
      toast("error", "asana_sync.toasts.error_title");
    } finally {
      setMapping(false);
    }
  }, [
    workspaceSlug, activeConnection, planeProjectId, asanaProjectGid, direction, defaultStateId,
    selectedRemoteProject, mutateSyncs, toast,
  ]);

  const handleRunNow = useCallback(
    async (syncId: string) => {
      setRunningId(syncId);
      try {
        await asanaSyncService.runWorkspaceSync(workspaceSlug, syncId);
        toast("success", "asana_sync.toasts.queued_title");
      } catch {
        toast("error", "asana_sync.toasts.error_title");
      } finally {
        setRunningId(null);
      }
    },
    [workspaceSlug, toast]
  );

  const handleShowLogs = useCallback(
    async (syncId: string) => {
      if (logsFor === syncId) {
        setLogsFor(null);
        setLogs([]);
        return;
      }
      const rows = await asanaSyncService.listWorkspaceSyncLogs(workspaceSlug, syncId).catch(() => [] as TAsanaSyncLog[]);
      setLogs(rows);
      setLogsFor(syncId);
    },
    [workspaceSlug, logsFor]
  );

  const selectClass =
    "w-full rounded-md border border-subtle bg-custom-background-100 px-3 py-2 text-sm outline-none focus:border-custom-primary";

  return (
    <section className="mt-8 border-t border-subtle pt-6">
      <h4 className="text-16 font-medium">{t("asana_sync.heading")}</h4>
      <p className="mt-1 text-sm text-custom-text-200">{t("asana_sync.description")}</p>

      {/* 1. Connection */}
      <div className="mt-4 rounded-md border border-subtle bg-custom-background-90 p-4">
        {activeConnection ? (
          <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-x-6 gap-y-2 text-sm">
              <span className="font-medium">{activeConnection.name}</span>
              <span className="text-custom-text-300">{activeConnection.pat_preview}</span>
              <span className="text-custom-text-300">
                {activeConnection.asana_workspace_name || t("asana_sync.not_verified")}
              </span>
              <span className={activeConnection.is_active ? "text-green-600" : "text-custom-text-300"}>
                {activeConnection.is_active ? t("asana_sync.active") : t("asana_sync.inactive")}
              </span>
            </div>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={handleVerify}
                disabled={verifying}
                className="rounded-md border border-subtle px-3 py-1.5 text-xs font-medium hover:bg-custom-background-80 disabled:opacity-50"
              >
                {verifying ? t("asana_sync.verifying") : t("asana_sync.verify")}
              </button>
              <button
                type="button"
                onClick={handleDisconnect}
                className="rounded-md border border-danger-base/40 px-3 py-1.5 text-xs font-medium text-danger-base hover:bg-danger-base/10"
              >
                {t("asana_sync.disconnect")}
              </button>
            </div>
          </div>
        ) : (
          <div className="space-y-3">
            <p className="text-sm text-custom-text-300">{t("asana_sync.connect_hint")}</p>
            <div className="grid gap-3 md:grid-cols-2">
              <div>
                <label className="mb-1 block text-xs font-medium">{t("asana_sync.connection_name")}</label>
                <input
                  className={selectClass}
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="Asana"
                />
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium">{t("asana_sync.pat_label")}</label>
                <input
                  className={selectClass}
                  type="password"
                  value={pat}
                  onChange={(e) => setPat(e.target.value)}
                  placeholder="1/2••••••"
                />
              </div>
            </div>
            <p className="text-xs text-custom-text-300">{t("asana_sync.pat_help")}</p>
            <button
              type="button"
              onClick={handleConnect}
              disabled={!pat.trim() || creating}
              className="rounded-md bg-custom-primary px-3 py-1.5 text-xs font-medium text-white hover:opacity-90 disabled:opacity-50"
            >
              {creating ? t("asana_sync.connecting") : t("asana_sync.connect")}
            </button>
          </div>
        )}
      </div>

      {/* 2. Map an Asana project to a Plane project */}
      {activeConnection && (
        <div className="mt-4 rounded-md border border-subtle bg-custom-background-90 p-4">
          <h5 className="text-sm font-medium">{t("asana_sync.map_heading")}</h5>
          <div className="mt-3 grid gap-3 md:grid-cols-2">
            <select className={selectClass} value={asanaProjectGid} onChange={(e) => setAsanaProjectGid(e.target.value)}>
              <option value="">{t("asana_sync.choose_asana_project")}</option>
              {(remoteProjects ?? []).map((p) => (
                <option key={p.gid} value={p.gid}>
                  {p.name}
                </option>
              ))}
            </select>
            <select
              className={selectClass}
              value={planeProjectId}
              onChange={(e) => {
                setPlaneProjectId(e.target.value);
                setDefaultStateId("");
              }}
            >
              <option value="">{t("asana_sync.choose_plane_project")}</option>
              {(projects ?? []).map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
            <select
              className={selectClass}
              value={direction}
              onChange={(e) => setDirection(e.target.value as TDirection)}
            >
              <option value="bidirectional">{t("asana_sync.direction_bidirectional")}</option>
              <option value="pull">{t("asana_sync.direction_pull")}</option>
              <option value="push">{t("asana_sync.direction_push")}</option>
            </select>
            <select
              className={selectClass}
              value={defaultStateId}
              onChange={(e) => setDefaultStateId(e.target.value)}
              disabled={!planeProjectId}
            >
              <option value="">{t("asana_sync.default_state_hint")}</option>
              {(projectStates ?? []).map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
          </div>
          <p className="mt-2 text-xs text-custom-text-300">{t("asana_sync.section_state_hint")}</p>
          <button
            type="button"
            onClick={handleMap}
            disabled={!planeProjectId || !asanaProjectGid || mapping}
            className="mt-3 rounded-md bg-custom-primary px-3 py-1.5 text-xs font-medium text-white hover:opacity-90 disabled:opacity-50"
          >
            {mapping ? t("asana_sync.mapping") : t("asana_sync.map_button")}
          </button>
        </div>
      )}

      {/* 3. Existing syncs */}
      {syncs && syncs.length > 0 && (
        <div className="mt-4 space-y-2">
          {syncs.map((sync) => (
            <div
              key={sync.id}
              className="flex flex-wrap items-center gap-x-4 gap-y-1 rounded-md border border-subtle p-3 text-sm"
            >
              <span className="font-medium">{sync.asana_project_name || sync.asana_project_gid}</span>
              <span className="text-custom-text-300">→ {sync.project_name}</span>
              <span className="rounded bg-custom-background-80 px-1.5 py-0.5 text-[11px]">{sync.direction}</span>
              <span className={sync.is_active ? "text-green-600" : "text-custom-text-300"}>
                {sync.is_active ? t("asana_sync.active") : t("asana_sync.inactive")}
              </span>
              <span className="text-xs text-custom-text-300">
                {sync.last_synced_at
                  ? new Date(sync.last_synced_at).toLocaleString()
                  : t("asana_sync.never_synced")}
              </span>
              <div className="ml-auto flex gap-2">
                <button
                  type="button"
                  onClick={() => handleShowLogs(sync.id)}
                  className="rounded-md border border-subtle px-3 py-1.5 text-xs font-medium hover:bg-custom-background-80"
                >
                  {logsFor === sync.id ? t("asana_sync.hide_logs") : t("asana_sync.view_logs")}
                </button>
                <button
                  type="button"
                  onClick={() => handleRunNow(sync.id)}
                  disabled={runningId === sync.id}
                  className="rounded-md border border-subtle px-3 py-1.5 text-xs font-medium hover:bg-custom-background-80 disabled:opacity-50"
                >
                  {runningId === sync.id ? t("asana_sync.running") : t("asana_sync.run_now")}
                </button>
              </div>
              {logsFor === sync.id && (
                <div className="w-full space-y-1 border-t border-subtle pt-2">
                  {logs.length === 0 ? (
                    <p className="text-xs text-custom-text-300">{t("asana_sync.no_logs")}</p>
                  ) : (
                    logs.slice(0, 30).map((log) => (
                      <div key={log.id} className="flex items-center gap-2 text-xs text-custom-text-300">
                        <StatusPill status={log.status} />
                        <span>{new Date(log.created_at).toLocaleString()}</span>
                        <span>
                          [{log.direction}/{log.entity_type}]
                        </span>
                        <span className="truncate">{log.message}</span>
                      </div>
                    ))
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

export const AsanaSyncSection = observer(AsanaSyncSectionBase);
