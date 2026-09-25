/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { observer } from "mobx-react";
import useSWR from "swr";
// components
import { EUserPermissions, EUserPermissionsLevel } from "@plane/constants";
import { NotAuthorizedView } from "@/components/auth-screens/not-authorized-view";
import { AsanaSyncSection } from "@/components/asana-sync/asana-sync-section";
import { PageHead } from "@/components/core/page-title";
import { SingleIntegrationCard } from "@/components/integration/single-integration-card";
import { GithubDeliverySettings } from "@/components/github-delivery/settings";
import { SlackDeliverySettings } from "@/components/slack-delivery/settings";
import { SettingsContentWrapper } from "@/components/settings/content-wrapper";
// constants
import { APP_INTEGRATIONS } from "@plane/constants";
// hooks
import { useWorkspace } from "@/hooks/store/use-workspace";
import { useUserPermissions } from "@/hooks/store/user";
// services
import { IntegrationService } from "@/services/integrations";
// local imports
import { IntegrationsWorkspaceSettingsHeader } from "./header";
import type { Route } from "./+types/page";

const integrationService = new IntegrationService();

function WorkspaceIntegrationsPage({ params }: Route.ComponentProps) {
  // router
  const { workspaceSlug } = params;
  // store hooks
  const { currentWorkspace } = useWorkspace();
  const { allowPermissions } = useUserPermissions();

  // derived values
  const isAdmin = allowPermissions([EUserPermissions.ADMIN], EUserPermissionsLevel.WORKSPACE);
  const pageTitle = currentWorkspace?.name ? `${currentWorkspace.name} - Integrations` : undefined;
  const { data: appIntegrations } = useSWR(isAdmin ? APP_INTEGRATIONS : null, () =>
    isAdmin ? integrationService.getAppIntegrationsList() : null
  );

  if (!isAdmin) return <NotAuthorizedView section="settings" className="h-auto" />;

  return (
    <SettingsContentWrapper header={<IntegrationsWorkspaceSettingsHeader />}>
      <PageHead title={pageTitle} />
      <section className="w-full">
        {currentWorkspace?.slug && <GithubDeliverySettings workspaceSlug={currentWorkspace.slug} />}
        {currentWorkspace?.slug && <SlackDeliverySettings workspaceSlug={currentWorkspace.slug} />}
        <div>
          {appIntegrations
            ? appIntegrations.map((integration) => (
                <SingleIntegrationCard key={integration.id} integration={integration} />
              ))
            : null}
        </div>
        {/* Fork feature: Asana <-> Plane bidirectional sync */}
        <AsanaSyncSection workspaceSlug={workspaceSlug} />
      </section>
    </SettingsContentWrapper>
  );
}

export default observer(WorkspaceIntegrationsPage);
