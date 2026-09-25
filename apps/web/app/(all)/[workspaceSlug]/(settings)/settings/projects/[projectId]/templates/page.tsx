/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { observer } from "mobx-react";
// plane imports
import { EUserPermissions, EUserPermissionsLevel } from "@plane/constants";
import { useTranslation } from "@plane/i18n";
// components
import { NotAuthorizedView } from "@/components/auth-screens/not-authorized-view";
import { PageHead } from "@/components/core/page-title";
import { IssueTemplateSettings } from "@/components/templates/issue-template-settings";
import { SettingsContentWrapper } from "@/components/settings/content-wrapper";
import { SettingsHeading } from "@/components/settings/heading";
// hooks
import { useProject } from "@/hooks/store/use-project";
import { useUserPermissions } from "@/hooks/store/user";
// local imports
import type { Route } from "./+types/page";
import { TemplatesProjectSettingsHeader } from "./header";

function TemplatesSettingsPage({ params }: Route.ComponentProps) {
  // router
  const { workspaceSlug, projectId } = params;
  // store hooks
  const { workspaceUserInfo, allowPermissions } = useUserPermissions();
  const { currentProjectDetails: projectDetails } = useProject();

  const { t } = useTranslation();

  // derived values: any project member can view templates (apply is a member action);
  // edits are gated inside the component for project admins.
  const canViewTemplates = allowPermissions(
    [EUserPermissions.ADMIN, EUserPermissions.MEMBER],
    EUserPermissionsLevel.PROJECT
  );

  // derived values
  const pageTitle = projectDetails?.name ? `${projectDetails?.name} - Templates` : undefined;

  if (workspaceUserInfo && !canViewTemplates) {
    return <NotAuthorizedView section="settings" isProjectView className="h-auto" />;
  }

  return (
    <SettingsContentWrapper header={<TemplatesProjectSettingsHeader />} hugging>
      <PageHead title={pageTitle} />
      <section className={`w-full ${canViewTemplates ? "" : "opacity-60"}`}>
        <SettingsHeading
          title={t("project_settings.templates.heading")}
          description={t("project_settings.templates.page_description")}
        />
        <div className="mt-6">
          {/* Fork feature: per-project work item templates */}
          <IssueTemplateSettings />
        </div>
      </section>
    </SettingsContentWrapper>
  );
}

export default observer(TemplatesSettingsPage);
