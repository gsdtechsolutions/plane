/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useCallback, useMemo, useState } from "react";
import { observer } from "mobx-react";
import { Pencil, Plus, Trash2 } from "lucide-react";
// plane imports
import { EUserPermissions, EUserPermissionsLevel } from "@plane/constants";
import { setToast } from "@plane/blocks/toast";
import { Button } from "@makeplane/propel/components/button";
import { useTranslation } from "@plane/i18n";
import type { IInfraConnection, TInfraService } from "@plane/types";
import { useSWRConfig } from "swr";
// hooks
import { useUserPermissions } from "@/hooks/store/user";
// services
import { InfraService } from "@/services/infra/infra.service";
// local imports
import { ConnectionModal } from "./connection-modal";

const infraService = new InfraService();

const SERVICE_BADGE_STYLES: Record<TInfraService, string> = {
  coolify: "bg-blue-3/20 text-blue-400",
  grafana: "bg-orange-3/20 text-orange-400",
};

export function ConnectionsPanel(props: { workspaceSlug: string; connections: IInfraConnection[] | undefined }) {
  const { workspaceSlug, connections } = props;
  // states
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [editingConnection, setEditingConnection] = useState<IInfraConnection | undefined>(undefined);
  const [verifyingId, setVerifyingId] = useState<string | undefined>(undefined);
  const [verifyResults, setVerifyResults] = useState<Record<string, { ok: boolean; text: string }>>({});
  const [deletingId, setDeletingId] = useState<string | undefined>(undefined);
  // hooks
  const { t } = useTranslation();
  const { mutate } = useSWRConfig();
  const { allowPermissions } = useUserPermissions();

  const isWorkspaceAdmin = allowPermissions([EUserPermissions.ADMIN], EUserPermissionsLevel.WORKSPACE);

  const refreshConnections = useCallback(() => {
    mutate(`infra-connections-${workspaceSlug}`);
  }, [mutate, workspaceSlug]);

  const sorted = useMemo(
    () => (connections ? [...connections].sort((a, b) => a.name.localeCompare(b.name)) : undefined),
    [connections]
  );

  const handleVerify = async (connection: IInfraConnection) => {
    setVerifyingId(connection.id);
    try {
      const result = await infraService.verifyConnection(workspaceSlug, connection.id);
      setVerifyResults((prev) => ({
        ...prev,
        [connection.id]: result.ok
          ? { ok: true, text: `${t("project_settings.infra.connections.verified")}${result.version ? ` — ${result.version}` : ""}` }
          : { ok: false, text: result.error ?? t("project_settings.infra.connections.verify_failed") },
      }));
    } catch (error) {
      setVerifyResults((prev) => ({
        ...prev,
        [connection.id]: {
          ok: false,
          text: (error as { error?: string })?.error ?? t("project_settings.infra.connections.verify_failed"),
        },
      }));
    } finally {
      setVerifyingId(undefined);
    }
  };

  const handleDelete = async (connection: IInfraConnection) => {
    if (!window.confirm(t("project_settings.infra.connections.delete_confirm"))) return;
    try {
      await infraService.deleteConnection(workspaceSlug, connection.id);
      setToast({
        type: "success",
        title: "Success!",
        message: t("project_settings.infra.connections.toasts.deleted"),
      });
      refreshConnections();
    } catch {
      setToast({
        type: "error",
        title: "Error!",
        message: t("project_settings.infra.connections.toasts.delete_error"),
      });
    } finally {
      setDeletingId(undefined);
    }
  };

  return (
    <section>
      <div className="flex items-start justify-between gap-4">
        <div>
          <h3 className="text-base font-medium">{t("project_settings.infra.connections.heading")}</h3>
          <p className="mt-1 text-sm text-tertiary">{t("project_settings.infra.connections.description")}</p>
        </div>
        {isWorkspaceAdmin && (
          <Button
            variant="primary"
            size="sm"
            stretch="auto"
            icon={<Plus className="size-3.5" />}
            label={t("project_settings.infra.connections.add")}
            onClick={() => {
              setEditingConnection(undefined);
              setIsModalOpen(true);
            }}
          />
        )}
      </div>

      {!isWorkspaceAdmin && (
        <p className="mt-2 text-xs text-tertiary">{t("project_settings.infra.connections.only_admins")}</p>
      )}

      <div className="mt-4 divide-y divide-subtle rounded-md border border-subtle">
        {!sorted || sorted.length === 0 ? (
          <p className="px-4 py-6 text-center text-sm text-tertiary">
            {t("project_settings.infra.connections.empty_state")}
          </p>
        ) : (
          sorted.map((connection) => {
            const verify = verifyResults[connection.id];
            return (
              <div key={connection.id} className="flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3">
                <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${SERVICE_BADGE_STYLES[connection.service]}`}>
                  {connection.service}
                </span>
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium">{connection.name}</p>
                  <p className="truncate text-xs text-tertiary">{connection.base_url}</p>
                </div>
                <span className="text-xs text-tertiary">
                  {connection.has_token ? "" : t("project_settings.infra.connections.no_token")}
                </span>
                <div className="ml-auto flex items-center gap-2">
                  {verify && (
                    <span className={`text-xs ${verify.ok ? "text-green-500" : "text-red-500"}`} title={verify.text}>
                      {verify.ok ? "✓ " : "✕ "}
                      {verify.text}
                    </span>
                  )}
                  {isWorkspaceAdmin && (
                    <>
                      <Button
                        variant="secondary"
                        size="sm"
                        stretch="auto"
                        loading={verifyingId === connection.id}
                        label={
                          verifyingId === connection.id
                            ? t("project_settings.infra.connections.verifying")
                            : t("project_settings.infra.connections.verify")
                        }
                        onClick={() => handleVerify(connection)}
                      />
                      <button
                        type="button"
                        className="grid size-6 place-items-center rounded-sm hover:bg-layer-1"
                        onClick={() => {
                          setEditingConnection(connection);
                          setIsModalOpen(true);
                        }}
                        aria-label={t("project_settings.infra.connections.heading")}
                      >
                        <Pencil className="size-3.5 text-tertiary" />
                      </button>
                      <button
                        type="button"
                        className="grid size-6 place-items-center rounded-sm hover:bg-layer-1"
                        onClick={() => handleDelete(connection)}
                        aria-label={t("project_settings.infra.connections.delete")}
                      >
                        <Trash2 className="size-3.5 text-tertiary hover:text-danger-primary" />
                      </button>
                    </>
                  )}
                </div>
              </div>
            );
          })
        )}
      </div>

      {isModalOpen && (
        <ConnectionModal
          workspaceSlug={workspaceSlug}
          connection={editingConnection}
          handleClose={() => {
            setIsModalOpen(false);
            setEditingConnection(undefined);
          }}
          onSaved={refreshConnections}
        />
      )}
    </section>
  );
}
