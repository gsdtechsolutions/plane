/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect } from "react";
import { observer } from "mobx-react";
// plane imports
import { EUserPermissions, EUserPermissionsLevel } from "@plane/constants";
import { setToast } from "@plane/blocks/toast";
import { useTranslation } from "@plane/i18n";
import type { IInfraConnection, IInfraLink } from "@plane/types";
import useSWR from "swr";
// components
import { NotAuthorizedView } from "@/components/auth-screens/not-authorized-view";
import { SettingsHeading } from "@/components/settings/heading";
// hooks
import { useUserPermissions } from "@/hooks/store/user";
// services
import { InfraService } from "@/services/infra/infra.service";
// local imports
import { BoardLinksPanel } from "./board-links-panel";
import { ConnectionsPanel } from "./connections-panel";
import { StatusPanel } from "./status-panel";

const infraService = new InfraService();

function InfraSettingsRoot({ workspaceSlug, projectId }: { workspaceSlug: string; projectId: string }) {
  // hooks
  const { t } = useTranslation();

  const { data: connections, error: connectionsError } = useSWR(
    `infra-connections-${workspaceSlug}`,
    () => infraService.fetchConnections(workspaceSlug) as Promise<IInfraConnection[]>,
    { revalidateIfStale: false, revalidateOnFocus: false, revalidateOnReconnect: false }
  );
  const { data: links, mutate: mutateLinks } = useSWR(
    `infra-links-${workspaceSlug}-${projectId}`,
    () => infraService.fetchLinks(workspaceSlug, projectId) as Promise<IInfraLink[]>,
    { revalidateIfStale: false, revalidateOnFocus: false, revalidateOnReconnect: false }
  );

  useEffect(() => {
    if (connectionsError) {
      setToast({
        type: "error",
        title: "Error!",
        message: t("project_settings.infra.connections.toasts.load_error"),
      });
    }
  }, [connectionsError, t]);

  const hasLinks = (links?.length ?? 0) > 0;

  return (
    <div className="flex flex-col gap-10">
      <SettingsHeading
        title={t("project_settings.infra.heading")}
        description={t("project_settings.infra.description")}
      />
      <ConnectionsPanel workspaceSlug={workspaceSlug} connections={connections} />
      <BoardLinksPanel
        workspaceSlug={workspaceSlug}
        projectId={projectId}
        connections={connections}
        links={links}
        refreshLinks={() => mutateLinks()}
      />
      <StatusPanel workspaceSlug={workspaceSlug} projectId={projectId} hasLinks={hasLinks} />
    </div>
  );
}

export default observer(InfraSettingsRoot);
