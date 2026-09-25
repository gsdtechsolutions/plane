/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useState } from "react";
import { ExternalLink, RefreshCw } from "lucide-react";
// plane imports
import { setToast } from "@plane/blocks/toast";
import { Button } from "@makeplane/propel/components/button";
import { useTranslation } from "@plane/i18n";
import type {
  IInfraCoolifyStatus,
  IInfraLinkStatusEntry,
} from "@plane/types";
import useSWR from "swr";
// services
import { InfraService } from "@/services/infra/infra.service";

const infraService = new InfraService();

const STATUS_STYLES: Record<string, string> = {
  running: "bg-green-500/15 text-green-500",
  restarting: "bg-amber-500/15 text-amber-500",
  stopped: "bg-red-500/15 text-red-500",
  exited: "bg-red-500/15 text-red-500",
};

function CoolifyStatusCard({ entry }: { entry: IInfraLinkStatusEntry }) {
  const { t } = useTranslation();
  const status = entry.status as IInfraCoolifyStatus | null;
  if (!status) {
    return (
      <p className="text-xs text-tertiary">{entry.error ?? t("project_settings.infra.status.error")}</p>
    );
  }
  const badgeStyle = STATUS_STYLES[(status.status ?? "").toLowerCase()] ?? "bg-layer-2 text-secondary";
  const version = status.version;
  const latestSuccessful = status.deployments.find((d) => (d.status ?? "").toLowerCase() === "finished") ?? status.deployments[0];

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className={`rounded-full px-2 py-0.5 text-xs font-medium capitalize ${badgeStyle}`}>
          {status.status ?? t("project_settings.infra.status.unknown")}
        </span>
        {version?.label && (
          <span className="text-xs text-secondary">
            {t("project_settings.infra.status.version")}: <span className="font-medium">{version.label}</span>
          </span>
        )}
        <span className="text-xs text-tertiary">
          {t("project_settings.infra.status.health")}:{" "}
          {status.health?.enabled
            ? `${t("project_settings.infra.status.health_on")}${status.health.path ? ` (${status.health.path})` : ""}`
            : t("project_settings.infra.status.health_off")}
        </span>
        {(status.deep_link || status.app_url) && (
          <a
            href={status.deep_link ?? status.app_url ?? "#"}
            target="_blank"
            rel="noopener noreferrer"
            className="ml-auto inline-flex items-center gap-1 text-xs font-medium text-primary hover:underline"
          >
            {t("project_settings.infra.status.open_in_coolify")}
            <ExternalLink className="size-3" />
          </a>
        )}
      </div>

      <div>
        <p className="text-xs font-medium text-secondary">{t("project_settings.infra.status.deployments")}</p>
        {status.deployments.length === 0 ? (
          <p className="mt-1 text-xs text-tertiary">{t("project_settings.infra.status.no_deployments")}</p>
        ) : (
          <ul className="mt-1 divide-y divide-subtle rounded-md border border-subtle">
            {status.deployments.map((deployment) => (
              <li key={deployment.id} className="flex items-center gap-3 px-3 py-1.5 text-xs">
                <span className="w-16 shrink-0 font-medium capitalize text-secondary">
                  {deployment.status ?? t("project_settings.infra.status.unknown")}
                </span>
                {deployment.commit_sha && (
                  <code className="shrink-0 rounded bg-layer-2 px-1.5 py-0.5 text-[11px]">
                    {deployment.commit_sha.slice(0, 7)}
                  </code>
                )}
                <span className="min-w-0 grow truncate text-tertiary">{deployment.message ?? ""}</span>
                {deployment === latestSuccessful && deployment.finished_at && (
                  <span className="shrink-0 text-tertiary">
                    {new Date(deployment.finished_at).toLocaleString()}
                  </span>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

export function StatusPanel(props: { workspaceSlug: string; projectId: string; hasLinks: boolean }) {
  const { workspaceSlug, projectId, hasLinks } = props;
  // states
  const [isRefreshing, setIsRefreshing] = useState(false);
  // hooks
  const { t } = useTranslation();

  const { data, mutate, isLoading } = useSWR(
    hasLinks ? `infra-status-${workspaceSlug}-${projectId}` : null,
    () => infraService.fetchStatus(workspaceSlug, projectId),
    {
      revalidateIfStale: false,
      revalidateOnFocus: false,
      revalidateOnReconnect: false,
      refreshInterval: 30_000,
    }
  );

  const handleRefresh = async () => {
    setIsRefreshing(true);
    try {
      await mutate(() => infraService.fetchStatus(workspaceSlug, projectId, true), {
        revalidate: false,
      });
    } catch {
      setToast({
        type: "error",
        title: "Error!",
        message: t("project_settings.infra.status.error"),
      });
    } finally {
      setIsRefreshing(false);
    }
  };

  if (!hasLinks) {
    return (
      <section>
        <h3 className="text-base font-medium">{t("project_settings.infra.status.heading")}</h3>
        <p className="mt-1 text-sm text-tertiary">{t("project_settings.infra.status.empty_state")}</p>
      </section>
    );
  }

  const entries = data?.links ?? [];

  return (
    <section>
      <div className="flex items-start justify-between gap-4">
        <div>
          <h3 className="text-base font-medium">{t("project_settings.infra.status.heading")}</h3>
          <p className="mt-1 text-sm text-tertiary">{t("project_settings.infra.status.description")}</p>
        </div>
        <Button
          variant="secondary"
          size="sm"
          stretch="auto"
          icon={<RefreshCw className="size-3.5" />}
          label={isRefreshing ? t("project_settings.infra.status.refreshing") : t("project_settings.infra.status.refresh")}
          loading={isRefreshing}
          onClick={handleRefresh}
        />
      </div>

      <div className="mt-4 flex flex-col gap-3">
        {isLoading && <p className="text-sm text-tertiary">…</p>}
        {entries.map((entry) => (
          <div key={entry.link.id} className="rounded-md border border-subtle px-4 py-3">
            <div className="mb-2 flex items-center gap-2">
              <p className="text-sm font-medium">{entry.link.display_name || entry.link.external_id}</p>
              <span className="rounded-full bg-layer-2 px-2 py-0.5 text-[11px] text-tertiary">
                {entry.link.connection_info?.name}
              </span>
            </div>
            {entry.link.kind === "coolify_app" ? (
              <CoolifyStatusCard entry={entry} />
            ) : (
              <div className="flex flex-wrap items-center gap-2">
                <a
                  href={entry.link.external_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1 text-xs font-medium text-primary hover:underline"
                >
                  {entry.link.display_name || entry.link.external_id}
                  <ExternalLink className="size-3" />
                </a>
                {entry.error && <span className="text-xs text-tertiary">{entry.error}</span>}
              </div>
            )}
          </div>
        ))}
      </div>
    </section>
  );
}
