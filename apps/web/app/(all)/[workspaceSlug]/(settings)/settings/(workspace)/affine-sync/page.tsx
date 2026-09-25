/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useState } from "react";
import { observer } from "mobx-react";
import useSWR from "swr";
// plane imports
import { EUserPermissions, EUserPermissionsLevel } from "@plane/constants";
import { useTranslation } from "@plane/i18n";
import { Button } from "@makeplane/propel/components/button";
import { Input } from "@makeplane/propel/components/input";
import { Switch } from "@makeplane/propel/components/switch";
import { setToast } from "@plane/blocks/toast";
import { ConfirmDialog } from "@plane/blocks/dialog";
import type {
  IAffineConnection,
  IAffineConflictStrategy,
  IAffinePageMap,
  IAffineSyncStats,
  IAffineWorkspaceSummary,
} from "@plane/types";
// components
import { NotAuthorizedView } from "@/components/auth-screens/not-authorized-view";
import { PageHead } from "@/components/core/page-title";
import { SettingsHeading } from "@/components/settings/heading";
import { SettingsContentWrapper } from "@/components/settings/content-wrapper";
// hooks
import { useWorkspace } from "@/hooks/store/use-workspace";
import { useProject } from "@/hooks/store/use-project";
import { useUserPermissions } from "@/hooks/store/user";
// services
import { AffineService } from "@/services/affine.service";
// local imports
import type { Route } from "./+types/page";
import { AffineWorkspaceSettingsHeader } from "./header";

const affineService = new AffineService();

type TFormErrors = Record<string, string | undefined>;

const CONFLICT_STRATEGIES: { value: IAffineConflictStrategy; i18nKey: string }[] = [
  { value: "newest_wins", i18nKey: "workspace_settings.settings.affine.connect.conflict_newest" },
  { value: "affine_wins", i18nKey: "workspace_settings.settings.affine.connect.conflict_affine" },
  { value: "plane_wins", i18nKey: "workspace_settings.settings.affine.connect.conflict_plane" },
];

function formatDateTime(value: string | null): string {
  if (!value) return "";
  try {
    return new Date(value).toLocaleString();
  } catch {
    return value;
  }
}

const AffineWorkspaceSettingsPage = observer(function AffineWorkspaceSettingsPage({ params }: Route.ComponentProps) {
  // router
  const { workspaceSlug } = params;
  // translation
  const { t } = useTranslation();
  // mobx store
  const { workspaceUserInfo, allowPermissions } = useUserPermissions();
  const { currentWorkspace } = useWorkspace();
  const { workspaceProjectIds, getProjectById } = useProject();
  // local state
  const [connection, setConnection] = useState<IAffineConnection | null>(null);
  const [connectionLoaded, setConnectionLoaded] = useState(false);
  // connect form state
  const [instanceUrl, setInstanceUrl] = useState("");
  const [apiToken, setApiToken] = useState("");
  const [remoteWorkspaces, setRemoteWorkspaces] = useState<IAffineWorkspaceSummary[]>([]);
  const [selectedAffineWorkspace, setSelectedAffineWorkspace] = useState("");
  const [selectedProject, setSelectedProject] = useState("");
  const [conflictStrategy, setConflictStrategy] = useState<IAffineConflictStrategy>("newest_wins");
  const [syncPlaneCreates, setSyncPlaneCreates] = useState(false);
  const [syncDeletions, setSyncDeletions] = useState(false);
  const [probing, setProbing] = useState(false);
  const [connecting, setConnecting] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [showDisconnect, setShowDisconnect] = useState(false);
  const [errors, setErrors] = useState<TFormErrors>({});
  // page maps
  const [pageMaps, setPageMaps] = useState<IAffinePageMap[] | null>(null);

  const canPerformWorkspaceAdminActions = allowPermissions([EUserPermissions.ADMIN], EUserPermissionsLevel.WORKSPACE);

  // existing connection (404 = none yet)
  useSWR(
    canPerformWorkspaceAdminActions && workspaceSlug ? `AFFINE_CONNECTION_${workspaceSlug}` : null,
    async () => {
      try {
        const data = await affineService.fetchConnection(workspaceSlug);
        setConnection(data);
        setInstanceUrl(data.affine_instance_url);
        setSelectedProject(data.project);
        setConflictStrategy(data.settings?.conflict_strategy ?? "newest_wins");
        setSyncPlaneCreates(data.settings?.sync_plane_creates ?? false);
        setSyncDeletions(data.settings?.sync_deletions ?? false);
      } catch {
        setConnection(null);
      } finally {
        setConnectionLoaded(true);
      }
      return null;
    },
    { revalidateOnFocus: false }
  );

  // mapped pages (only when connected)
  useSWR(
    connection ? `AFFINE_PAGES_${workspaceSlug}` : null,
    async () => {
      try {
        setPageMaps(await affineService.fetchPageMaps(workspaceSlug));
      } catch {
        setPageMaps(null);
      }
      return null;
    },
    { revalidateOnFocus: false }
  );

  const notify = (title: string, type: "success" | "error" = "success") =>
    setToast({ type, title, message: "" });

  const handleProbe = async () => {
    const nextErrors: TFormErrors = {};
    if (!instanceUrl.trim()) nextErrors.instance_url = t("workspace_settings.settings.affine.errors.url_required");
    if (!apiToken.trim()) nextErrors.api_token = t("workspace_settings.settings.affine.errors.token_required");
    setErrors(nextErrors);
    if (Object.keys(nextErrors).length > 0) return;
    setProbing(true);
    try {
      const response = await affineService.probeWorkspaces(workspaceSlug, {
        instance_url: instanceUrl.trim(),
        api_token: apiToken.trim(),
      });
      setRemoteWorkspaces(response.workspaces ?? []);
      if (response.workspaces?.length === 1) setSelectedAffineWorkspace(response.workspaces[0].id);
    } catch (error: unknown) {
      notify((error as { error?: string })?.error || t("workspace_settings.settings.affine.errors.probe_failed"), "error");
    } finally {
      setProbing(false);
    }
  };

  const handleConnect = async () => {
    if (!selectedAffineWorkspace) {
      setErrors({ affine_workspace: t("workspace_settings.settings.affine.errors.workspace_required") });
      return;
    }
    if (!selectedProject) {
      setErrors({ project: t("workspace_settings.settings.affine.errors.project_required") });
      return;
    }
    setConnecting(true);
    try {
      const data = await affineService.createConnection(workspaceSlug, {
        affine_instance_url: instanceUrl.trim(),
        api_token: apiToken.trim(),
        affine_workspace_id: selectedAffineWorkspace,
        project: selectedProject,
        settings: {
          conflict_strategy: conflictStrategy,
          direction: "two_way",
          sync_plane_creates: syncPlaneCreates,
          sync_deletions: syncDeletions,
        },
      });
      setConnection(data);
      setApiToken("");
      notify(
        `${t("workspace_settings.settings.affine.status.connected_to")} ${data.affine_workspace_name || data.affine_workspace_id}`
      );
    } catch (error: unknown) {
      const apiError = error as { error?: string; affine_instance_url?: string[]; affine_workspace_id?: string[] };
      notify(
        apiError?.error ||
          apiError?.affine_instance_url?.[0] ||
          apiError?.affine_workspace_id?.[0] ||
          t("workspace_settings.settings.affine.errors.probe_failed"),
        "error"
      );
    } finally {
      setConnecting(false);
    }
  };

  const handleUpdateSettings = async (updates: Partial<IAffineConnection>) => {
    if (!connection) return;
    try {
      const data = await affineService.updateConnection(workspaceSlug, updates);
      setConnection(data);
    } catch (error: unknown) {
      notify((error as { error?: string })?.error || "Update failed", "error");
    }
  };

  const handleSyncNow = async () => {
    if (!connection) return;
    setSyncing(true);
    try {
      const response = await affineService.triggerSync(workspaceSlug);
      if (response.queued) {
        notify(t("workspace_settings.settings.affine.status.queued"));
      } else {
        const stats = response.stats as IAffineSyncStats | undefined;
        notify(
          `↑${stats?.pushed ?? 0} ↓${stats?.pulled ?? 0} +${(stats?.created_plane ?? 0) + (stats?.created_affine ?? 0)} ⚠${stats?.conflicts ?? 0} ✗${stats?.errors ?? 0}`
        );
        setPageMaps(await affineService.fetchPageMaps(workspaceSlug).catch(() => null));
        setConnection(await affineService.fetchConnection(workspaceSlug).catch(() => connection));
      }
    } catch (error: unknown) {
      notify((error as { error?: string })?.error || t("workspace_settings.settings.affine.status.error"), "error");
    } finally {
      setSyncing(false);
    }
  };

  const handleDisconnect = async () => {
    try {
      await affineService.deleteConnection(workspaceSlug);
      setConnection(null);
      setPageMaps(null);
      setRemoteWorkspaces([]);
      setSelectedAffineWorkspace("");
    } catch (error: unknown) {
      notify((error as { error?: string })?.error || "Disconnect failed", "error");
    }
  };

  const pageTitle = currentWorkspace?.name
    ? `${currentWorkspace.name} - ${t("workspace_settings.settings.affine.title")}`
    : undefined;

  if (workspaceUserInfo && !canPerformWorkspaceAdminActions) {
    return <NotAuthorizedView section="settings" className="h-auto" />;
  }
  if (!connectionLoaded) {
    return (
      <SettingsContentWrapper header={<AffineWorkspaceSettingsHeader />}>
        <PageHead title={pageTitle} />
      </SettingsContentWrapper>
    );
  }

  const statusLabel =
    connection?.last_sync_status === "ok"
      ? t("workspace_settings.settings.affine.status.status_ok")
      : connection?.last_sync_status === "error"
        ? t("workspace_settings.settings.affine.status.status_error")
        : t("workspace_settings.settings.affine.status.status_never");

  const selectClassName = "w-full rounded-md border border-subtle bg-layer-1 px-3 py-2 text-sm text-primary";

  return (
    <SettingsContentWrapper header={<AffineWorkspaceSettingsHeader />}>
      <PageHead title={pageTitle} />
      <div className="w-full space-y-6">
        <SettingsHeading
          title={t("workspace_settings.settings.affine.title")}
          description={t("workspace_settings.settings.affine.description")}
        />

        {!connection ? (
          /* ------------------------------------------------ CONNECT FORM */
          <section className="rounded-md border border-subtle bg-layer-1 p-6">
            <h3 className="text-h6-medium text-primary">{t("workspace_settings.settings.affine.connect.title")}</h3>
            <p className="mt-1 text-caption-md-regular text-tertiary">
              {t("workspace_settings.settings.affine.connect.description")}
            </p>
            <div className="mt-4 grid grid-cols-1 gap-4 md:grid-cols-2">
              <div className="flex flex-col gap-1">
                <label htmlFor="affineInstanceUrl">
                  {t("workspace_settings.settings.affine.connect.instance_url")}
                </label>
                <Input
                  size="2xl"
                  id="affineInstanceUrl"
                  type="text"
                  value={instanceUrl}
                  onChange={(e) => setInstanceUrl(e.target.value)}
                  placeholder={t("workspace_settings.settings.affine.connect.instance_url_placeholder")}
                  disabled={probing || connecting}
                />
                {errors.instance_url && <span className="text-11 text-danger-primary">{errors.instance_url}</span>}
              </div>
              <div className="flex flex-col gap-1">
                <label htmlFor="affineApiToken">{t("workspace_settings.settings.affine.connect.api_token")}</label>
                <Input
                  size="2xl"
                  id="affineApiToken"
                  type="password"
                  value={apiToken}
                  onChange={(e) => setApiToken(e.target.value)}
                  placeholder={t("workspace_settings.settings.affine.connect.api_token_placeholder")}
                  disabled={probing || connecting}
                />
                {errors.api_token && <span className="text-11 text-danger-primary">{errors.api_token}</span>}
              </div>
            </div>
            <div className="mt-4">
              <Button
                variant="primary"
                size="md"
                stretch="auto"
                loading={probing}
                onClick={handleProbe}
                label={t("workspace_settings.settings.affine.connect.list_workspaces")}
              />
            </div>
            {remoteWorkspaces.length > 0 && (
              <div className="mt-4 grid grid-cols-1 gap-4 md:grid-cols-2">
                <div className="flex flex-col gap-1">
                  <label htmlFor="affineWorkspaceSelect">
                    {t("workspace_settings.settings.affine.connect.select_workspace")}
                  </label>
                  <select
                    id="affineWorkspaceSelect"
                    className={selectClassName}
                    value={selectedAffineWorkspace}
                    onChange={(e) => setSelectedAffineWorkspace(e.target.value)}
                    disabled={connecting}
                  >
                    <option value="">—</option>
                    {remoteWorkspaces.map((ws) => (
                      <option key={ws.id} value={ws.id}>
                        {ws.name || ws.id}
                      </option>
                    ))}
                  </select>
                  {errors.affine_workspace && (
                    <span className="text-11 text-danger-primary">{errors.affine_workspace}</span>
                  )}
                </div>
                <div className="flex flex-col gap-1">
                  <label htmlFor="affineProjectSelect">
                    {t("workspace_settings.settings.affine.connect.select_project")}
                  </label>
                  <select
                    id="affineProjectSelect"
                    className={selectClassName}
                    value={selectedProject}
                    onChange={(e) => setSelectedProject(e.target.value)}
                    disabled={connecting}
                  >
                    <option value="">—</option>
                    {(workspaceProjectIds ?? []).map((projectId: string) => {
                      const project = getProjectById(projectId);
                      if (!project?.archived_at) {
                        return (
                          <option key={projectId} value={projectId}>
                            {project?.name ?? projectId}
                          </option>
                        );
                      }
                      return null;
                    })}
                  </select>
                  {errors.project && <span className="text-11 text-danger-primary">{errors.project}</span>}
                </div>
                <div className="flex flex-col gap-1">
                  <label htmlFor="affineConflictSelect">
                    {t("workspace_settings.settings.affine.connect.conflict_strategy")}
                  </label>
                  <select
                    id="affineConflictSelect"
                    className={selectClassName}
                    value={conflictStrategy}
                    onChange={(e) => setConflictStrategy(e.target.value as IAffineConflictStrategy)}
                    disabled={connecting}
                  >
                    {CONFLICT_STRATEGIES.map((strategy) => (
                      <option key={strategy.value} value={strategy.value}>
                        {t(strategy.i18nKey)}
                      </option>
                    ))}
                  </select>
                </div>
              </div>
            )}
            {remoteWorkspaces.length > 0 && (
              <div className="mt-4 space-y-3">
                <div className="flex items-center justify-between gap-4">
                  <span className="text-caption-md-regular text-secondary">
                    {t("workspace_settings.settings.affine.connect.sync_plane_creates")}
                  </span>
                  <Switch size="md" checked={syncPlaneCreates} onCheckedChange={() => setSyncPlaneCreates((v) => !v)} />
                </div>
                <div className="flex items-center justify-between gap-4">
                  <span className="text-caption-md-regular text-secondary">
                    {t("workspace_settings.settings.affine.connect.sync_deletions")}
                  </span>
                  <Switch size="md" checked={syncDeletions} onCheckedChange={() => setSyncDeletions((v) => !v)} />
                </div>
              </div>
            )}
            {remoteWorkspaces.length > 0 && (
              <div className="mt-5">
                <Button
                  variant="primary"
                  size="md"
                  stretch="auto"
                  loading={connecting}
                  onClick={handleConnect}
                  label={t("workspace_settings.settings.affine.connect.connect_button")}
                />
              </div>
            )}
          </section>
        ) : (
          /* ------------------------------------------------ CONNECTION VIEW */
          <>
            <section className="rounded-md border border-subtle bg-layer-1 p-6">
              <div className="flex flex-wrap items-center justify-between gap-4">
                <div>
                  <div className="text-caption-md-regular text-tertiary">
                    {t("workspace_settings.settings.affine.status.connected_to")}
                  </div>
                  <div className="mt-1 text-h6-medium text-primary">
                    {connection.affine_workspace_name || connection.affine_workspace_id}
                    <span className="ml-2 text-caption-md-regular text-tertiary">
                      {connection.affine_instance_url}
                    </span>
                  </div>
                  <div className="mt-2 flex flex-wrap items-center gap-3 text-caption-md-regular text-secondary">
                    <span>
                      {t("workspace_settings.settings.affine.status.last_sync")}:{" "}
                      {connection.last_synced_at
                        ? formatDateTime(connection.last_synced_at)
                        : t("workspace_settings.settings.affine.status.never_synced")}
                    </span>
                    <span className="rounded-sm bg-layer-2 px-2 py-0.5">{statusLabel}</span>
                    <span>{t("workspace_settings.settings.affine.status.pages", { count: connection.page_count })}</span>
                    {connection.conflict_count > 0 && (
                      <span className="text-danger-primary">
                        {t("workspace_settings.settings.affine.status.conflicts", { count: connection.conflict_count })}
                      </span>
                    )}
                    {!connection.is_active && (
                      <span className="rounded-sm bg-layer-2 px-2 py-0.5">
                        {t("workspace_settings.settings.affine.status.paused")}
                      </span>
                    )}
                  </div>
                  {connection.last_sync_error && (
                    <pre className="mt-3 max-h-40 overflow-auto whitespace-pre-wrap rounded-sm bg-layer-2 p-3 text-caption-md-regular text-danger-primary">
                      {connection.last_sync_error}
                    </pre>
                  )}
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <Button
                    variant="primary"
                    size="md"
                    stretch="auto"
                    loading={syncing}
                    onClick={handleSyncNow}
                    disabled={!connection.is_active}
                    label={
                      syncing
                        ? t("workspace_settings.settings.affine.status.syncing")
                        : t("workspace_settings.settings.affine.status.sync_now")
                    }
                  />
                  <Button
                    variant="secondary"
                    size="md"
                    stretch="auto"
                    onClick={() => handleUpdateSettings({ is_active: !connection.is_active })}
                    label={
                      connection.is_active
                        ? t("workspace_settings.settings.affine.status.pause")
                        : t("workspace_settings.settings.affine.status.resume")
                    }
                  />
                  <Button
                    variant="danger-outline"
                    size="md"
                    stretch="auto"
                    onClick={() => setShowDisconnect(true)}
                    label={t("workspace_settings.settings.affine.status.disconnect")}
                  />
                </div>
              </div>
            </section>

            {/* -------------------------------------------- MAPPED PAGES */}
            <section className="rounded-md border border-subtle bg-layer-1">
              <div className="border-b border-subtle p-4">
                <h3 className="text-h6-medium text-primary">{t("workspace_settings.settings.affine.pages.title")}</h3>
              </div>
              {(!pageMaps || pageMaps.length === 0) ? (
                <p className="p-4 text-caption-md-regular text-tertiary">
                  {t("workspace_settings.settings.affine.pages.empty")}
                </p>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-caption-md-regular">
                    <thead>
                      <tr className="border-b border-subtle text-left text-tertiary">
                        <th className="p-3">{t("workspace_settings.settings.affine.pages.doc")}</th>
                        <th className="p-3">{t("workspace_settings.settings.affine.pages.page")}</th>
                        <th className="p-3">{t("workspace_settings.settings.affine.pages.direction")}</th>
                        <th className="p-3">{t("workspace_settings.settings.affine.pages.status")}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {pageMaps.map((map) => (
                        <tr key={map.id} className="border-b border-subtle last:border-b-0">
                          <td className="max-w-64 truncate p-3 text-primary">
                            {map.affine_doc_title || map.affine_doc_id}
                          </td>
                          <td className="max-w-64 truncate p-3 text-secondary">
                            {map.page_detail?.name || map.page}
                          </td>
                          <td className="p-3 text-secondary">
                            {t(
                              map.last_sync_direction === "pull"
                                ? "workspace_settings.settings.affine.pages.pull"
                                : map.last_sync_direction === "push"
                                  ? "workspace_settings.settings.affine.pages.push"
                                  : map.last_sync_direction === "create"
                                    ? "workspace_settings.settings.affine.pages.create"
                                    : "workspace_settings.settings.affine.pages.none"
                            )}
                          </td>
                          <td className="p-3">
                            <span
                              className={`rounded-sm px-2 py-0.5 ${
                                map.status === "synced"
                                  ? "bg-layer-2 text-secondary"
                                  : map.status === "conflict"
                                    ? "bg-amber-500/10 text-amber-500"
                                    : map.status === "error"
                                      ? "bg-red-500/10 text-red-500"
                                      : "bg-layer-2 text-tertiary"
                              }`}
                              title={map.last_error || undefined}
                            >
                              {t(
                                map.status === "synced"
                                  ? "workspace_settings.settings.affine.pages.status_synced"
                                  : map.status === "conflict"
                                    ? "workspace_settings.settings.affine.pages.status_conflict"
                                    : map.status === "error"
                                      ? "workspace_settings.settings.affine.pages.status_error"
                                      : "workspace_settings.settings.affine.pages.status_pending"
                              )}
                            </span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>
          </>
        )}
      </div>

      <ConfirmDialog
        isOpen={showDisconnect}
        isSubmitting={false}
        handleClose={() => setShowDisconnect(false)}
        handleSubmit={handleDisconnect}
        title={t("workspace_settings.settings.affine.status.disconnect_confirm_title")}
        content={t("workspace_settings.settings.affine.status.disconnect_confirm_message")}
        primaryButtonText={{
          loading: t("workspace_settings.settings.affine.status.disconnecting"),
          default: t("workspace_settings.settings.affine.status.disconnect"),
        }}
        secondaryButtonText={t("cancel")}
      />
    </SettingsContentWrapper>
  );
});

export default AffineWorkspaceSettingsPage;
