/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useCallback, useMemo, useState } from "react";
import { Trash2 } from "lucide-react";
// plane imports
import { EUserPermissions, EUserPermissionsLevel } from "@plane/constants";
import { setToast } from "@plane/blocks/toast";
import { Button } from "@makeplane/propel/components/button";
import { Input } from "@makeplane/propel/components/input";
import { useTranslation } from "@plane/i18n";
import type { IInfraConnection, IInfraLink } from "@plane/types";
import useSWR from "swr";
// hooks
import { useUserPermissions } from "@/hooks/store/user";
// services
import { InfraService } from "@/services/infra/infra.service";
// local imports
import { ResourceSelect } from "./resource-select";

const infraService = new InfraService();

function LinkRow(props: { workspaceSlug: string; projectId: string; link: IInfraLink; canWrite: boolean; onDeleted: () => void }) {
  const { workspaceSlug, projectId, link, canWrite, onDeleted } = props;
  const { t } = useTranslation();

  const handleDelete = async () => {
    try {
      await infraService.deleteLink(workspaceSlug, projectId, link.id);
      setToast({
        type: "success",
        title: "Success!",
        message: t("project_settings.infra.links.toasts.deleted"),
      });
      onDeleted();
    } catch {
      setToast({
        type: "error",
        title: "Error!",
        message: t("project_settings.infra.links.toasts.delete_error"),
      });
    }
  };

  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 px-4 py-2.5">
      <span className="rounded-full bg-layer-2 px-2 py-0.5 text-[11px] font-medium text-secondary">
        {link.kind === "coolify_app" ? "Coolify" : "Grafana"}
      </span>
      <div className="min-w-0">
        <p className="truncate text-sm font-medium">{link.display_name || link.external_id}</p>
        <p className="truncate text-xs text-tertiary">{link.connection_info?.name}</p>
      </div>
      <div className="ml-auto flex items-center gap-3">
        {link.external_url && (
          <a
            href={link.external_url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-xs font-medium text-primary hover:underline"
          >
            {link.external_url.replace(/^https?:\/\//, "").slice(0, 48)}
          </a>
        )}
        {canWrite && (
          <button
            type="button"
            className="grid size-6 place-items-center rounded-sm hover:bg-layer-1"
            onClick={handleDelete}
            aria-label={t("project_settings.infra.links.delete")}
          >
            <Trash2 className="size-3.5 text-tertiary hover:text-danger-primary" />
          </button>
        )}
      </div>
    </div>
  );
}

export function BoardLinksPanel(props: {
  workspaceSlug: string;
  projectId: string;
  connections: IInfraConnection[] | undefined;
  links: IInfraLink[] | undefined;
  refreshLinks: () => void;
}) {
  const { workspaceSlug, projectId, connections, links, refreshLinks } = props;
  // states
  const [coolifyConnectionId, setCoolifyConnectionId] = useState("");
  const [grafanaConnectionId, setGrafanaConnectionId] = useState("");
  const [manualUrl, setManualUrl] = useState("");
  const [isAdding, setIsAdding] = useState(false);
  // hooks
  const { t } = useTranslation();
  const { allowPermissions } = useUserPermissions();
  const canWrite = allowPermissions([EUserPermissions.ADMIN], EUserPermissionsLevel.PROJECT);

  const coolifyConnections = useMemo(
    () => (connections ?? []).filter((connection) => connection.service === "coolify"),
    [connections]
  );
  const grafanaConnections = useMemo(
    () => (connections ?? []).filter((connection) => connection.service === "grafana"),
    [connections]
  );

  const resourcesKey = useCallback(
    (connectionId: string) => (connectionId ? `infra-resources-${connectionId}` : null),
    []
  );
  const { data: coolifyResources } = useSWR(resourcesKey(coolifyConnectionId), () =>
    infraService.fetchConnectionResources(workspaceSlug, coolifyConnectionId)
  );
  const { data: grafanaResources } = useSWR(resourcesKey(grafanaConnectionId), () =>
    infraService.fetchConnectionResources(workspaceSlug, grafanaConnectionId)
  );

  const handleAddCoolifyApp = async (appExternalId: string) => {
    const connection = coolifyConnections.find((c) => c.id === coolifyConnectionId);
    const resource = coolifyResources?.resources.find((r) => r.id === appExternalId);
    if (!connection || !appExternalId) return;
    setIsAdding(true);
    try {
      await infraService.createLink(workspaceSlug, projectId, {
        connection: connection.id,
        kind: "coolify_app",
        external_id: appExternalId,
        display_name: resource?.name ?? "",
      });
      setToast({
        type: "success",
        title: "Success!",
        message: t("project_settings.infra.links.toasts.created"),
      });
      refreshLinks();
    } catch (error) {
      setToast({
        type: "error",
        title: "Error!",
        message:
          (error as { external_id?: string[] })?.external_id?.[0] ??
          (error as { error?: string })?.error ??
          t("project_settings.infra.links.toasts.save_error"),
      });
    } finally {
      setIsAdding(false);
    }
  };

  const handleAddDashboard = async (dashboardUid: string) => {
    const connection = grafanaConnections.find((c) => c.id === grafanaConnectionId);
    if (!connection) return;
    setIsAdding(true);
    try {
      await infraService.createLink(workspaceSlug, projectId, {
        connection: connection.id,
        kind: "grafana_dashboard",
        ...(dashboardUid
          ? { external_id: dashboardUid }
          : { external_url: manualUrl.trim() }),
      });
      setToast({
        type: "success",
        title: "Success!",
        message: t("project_settings.infra.links.toasts.created"),
      });
      setManualUrl("");
      refreshLinks();
    } catch (error) {
      setToast({
        type: "error",
        title: "Error!",
        message:
          (error as { external_url?: string[] })?.external_url?.[0] ??
          (error as { error?: string })?.error ??
          t("project_settings.infra.links.toasts.save_error"),
      });
    } finally {
      setIsAdding(false);
    }
  };

  const sortedLinks = useMemo(
    () => (links ? [...links].sort((a, b) => a.kind.localeCompare(b.kind)) : undefined),
    [links]
  );

  return (
    <section>
      <div>
        <h3 className="text-base font-medium">{t("project_settings.infra.links.heading")}</h3>
        <p className="mt-1 text-sm text-tertiary">{t("project_settings.infra.links.description")}</p>
      </div>

      <div className="mt-4 divide-y divide-subtle rounded-md border border-subtle">
        {!sortedLinks || sortedLinks.length === 0 ? (
          <p className="px-4 py-6 text-center text-sm text-tertiary">
            {t("project_settings.infra.links.empty_state")}
          </p>
        ) : (
          sortedLinks.map((link) => (
            <LinkRow
              key={link.id}
              workspaceSlug={workspaceSlug}
              projectId={projectId}
              link={link}
              canWrite={canWrite}
              onDeleted={refreshLinks}
            />
          ))
        )}
      </div>

      {canWrite && (
        <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-2">
          {/* Coolify application link */}
          <div className="rounded-md border border-subtle p-4">
            <p className="text-sm font-medium">{t("project_settings.infra.links.coolify_app")}</p>
            <div className="mt-3 flex flex-col gap-2">
              <ResourceSelect
                placeholder={t("project_settings.infra.links.select_connection")}
                options={coolifyConnections.map((connection) => ({ value: connection.id, label: connection.name }))}
                value={coolifyConnectionId}
                onChange={setCoolifyConnectionId}
              />
              {coolifyConnectionId && (
                <ResourceSelect
                  placeholder={t("project_settings.infra.links.select_app")}
                  options={(coolifyResources?.resources ?? []).map((resource) => ({
                    value: resource.id,
                    label: resource.name,
                  }))}
                  value=""
                  onChange={(selectedId) => {
                    if (selectedId) handleAddCoolifyApp(selectedId);
                  }}
                />
              )}
            </div>
          </div>

          {/* Grafana dashboard link */}
          <div className="rounded-md border border-subtle p-4">
            <p className="text-sm font-medium">{t("project_settings.infra.links.dashboard")}</p>
            <div className="mt-3 flex flex-col gap-2">
              <ResourceSelect
                placeholder={t("project_settings.infra.links.select_connection")}
                options={grafanaConnections.map((connection) => ({ value: connection.id, label: connection.name }))}
                value={grafanaConnectionId}
                onChange={setGrafanaConnectionId}
              />
              {grafanaConnectionId && (
                <>
                  <ResourceSelect
                    placeholder={t("project_settings.infra.links.select_dashboard")}
                    options={(grafanaResources?.resources ?? []).map((resource) => ({
                      value: resource.id,
                      label: resource.title ?? resource.name ?? resource.id,
                    }))}
                    value=""
                    onChange={(selectedUid) => {
                      if (selectedUid) handleAddDashboard(selectedUid);
                    }}
                  />
                  <div className="flex items-center gap-2">
                    <Input
                      size="2xl"
                      value={manualUrl}
                      onChange={(event) => setManualUrl(event.target.value)}
                      placeholder={t("project_settings.infra.links.manual_url_placeholder")}
                    />
                    <Button
                      variant="secondary"
                      size="md"
                      stretch="auto"
                      loading={isAdding}
                      label={t("project_settings.infra.links.add")}
                      onClick={() => {
                        if (!manualUrl.trim()) {
                          setToast({
                            type: "error",
                            title: "Error!",
                            message: t("project_settings.infra.links.toasts.validation"),
                          });
                          return;
                        }
                        handleAddDashboard("");
                      }}
                    />
                  </div>
                </>
              )}
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
