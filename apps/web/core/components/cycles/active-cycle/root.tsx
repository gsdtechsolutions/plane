/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useState } from "react";
import { Collapsible } from "@base-ui/react/collapsible";
import { observer } from "mobx-react";
import { useTheme } from "next-themes";
import { EmptyStateDetailed } from "@plane/blocks/empty-state";
// plane imports
import { useTranslation } from "@plane/i18n";
import type { ICycle } from "@plane/types";
import { Row } from "@plane/blocks/layout";
// assets
import darkActiveCycleAsset from "@/app/assets/empty-state/cycle/active-dark.webp?url";
import lightActiveCycleAsset from "@/app/assets/empty-state/cycle/active-light.webp?url";
// components
import { ActiveCycleStats } from "@/components/cycles/active-cycle/cycle-stats";
import { ActiveCycleProductivity } from "@/components/cycles/active-cycle/productivity";
import { ActiveCycleProgress } from "@/components/cycles/active-cycle/progress";
import useCyclesDetails from "@/components/cycles/active-cycle/use-cycles-details";
import { CycleListGroupHeader } from "@/components/cycles/list/cycle-list-group-header";
import { CyclesListItem } from "@/components/cycles/list/cycles-list-item";
// hooks
import { useCycle } from "@/hooks/store/use-cycle";
import { useProject } from "@/hooks/store/use-project";
import type { ActiveCycleIssueDetails } from "@/store/issue/cycle";

interface IActiveCycleDetails {
  workspaceSlug: string;
  projectId: string;
  cycleId?: string;
  showHeader?: boolean;
}

type ActiveCyclesComponentProps = {
  cycleId: string | null | undefined;
  activeCycle: ICycle | null;
  activeCycleResolvedPath: string;
  workspaceSlug: string;
  projectId: string;
  handleFiltersUpdate: (filters: any) => void;
  cycleIssueDetails?: ActiveCycleIssueDetails | { nextPageResults: boolean };
};

const ActiveCyclesComponent = observer(function ActiveCyclesComponent({
  cycleId,
  activeCycle,
  activeCycleResolvedPath: _activeCycleResolvedPath,
  workspaceSlug,
  projectId,
  handleFiltersUpdate,
  cycleIssueDetails,
}: ActiveCyclesComponentProps) {
  const { t } = useTranslation();

  if (!cycleId || !activeCycle) {
    return (
      <EmptyStateDetailed
        assetKey="cycle"
        title={t("project_cycles.empty_state.active.title")}
        description={t("project_cycles.empty_state.active.description")}
        rootClassName="py-10 h-auto"
      />
    );
  }

  return (
    <div className="flex flex-col border-b border-subtle">
      <CyclesListItem
        key={cycleId}
        cycleId={cycleId}
        workspaceSlug={workspaceSlug}
        projectId={projectId}
        className="!border-b-transparent"
      />
      <Row className="bg-surface-1 pt-3 pb-6">
        <div className="grid grid-cols-1 gap-3 bg-surface-1 lg:grid-cols-2 xl:grid-cols-3">
          <ActiveCycleProgress
            handleFiltersUpdate={handleFiltersUpdate}
            projectId={projectId}
            workspaceSlug={workspaceSlug}
            cycle={activeCycle}
          />
          <ActiveCycleProductivity workspaceSlug={workspaceSlug} projectId={projectId} cycle={activeCycle} />
          <ActiveCycleStats
            workspaceSlug={workspaceSlug}
            projectId={projectId}
            cycle={activeCycle}
            cycleId={cycleId}
            handleFiltersUpdate={handleFiltersUpdate}
            cycleIssueDetails={cycleIssueDetails}
          />
        </div>
      </Row>
    </div>
  );
});

/**
 * @description Orca Custom sidecar wrapper to handle active cycles individually.
 * Fetches the detail and issue statistics for a specific cycle and renders ActiveCyclesComponent.
 */
const ActiveCycleItemWrapper = observer(function ActiveCycleItemWrapper({
  workspaceSlug,
  projectId,
  cycleId,
  activeCycleResolvedPath,
}: {
  workspaceSlug: string;
  projectId: string;
  cycleId: string;
  activeCycleResolvedPath: string;
}) {
  const { handleFiltersUpdate, cycle: activeCycle, cycleIssueDetails } = useCyclesDetails({
    workspaceSlug,
    projectId,
    cycleId,
  });

  return (
    <ActiveCyclesComponent
      cycleId={cycleId}
      activeCycle={activeCycle}
      activeCycleResolvedPath={activeCycleResolvedPath}
      workspaceSlug={workspaceSlug}
      projectId={projectId}
      handleFiltersUpdate={handleFiltersUpdate}
      cycleIssueDetails={cycleIssueDetails}
    />
  );
});

/**
 * @description Component root displaying active cycles of a project.
 * Custom behavior (orca port): If `parallel_cycles` is enabled for the project, it displays
 * multiple concurrent active cycles. Otherwise, falls back to displaying a single active cycle.
 */
export const ActiveCycleRoot = observer(function ActiveCycleRoot(props: IActiveCycleDetails) {
  const { workspaceSlug, projectId, cycleId: propsCycleId, showHeader = true } = props;
  // theme hook
  const { resolvedTheme } = useTheme();
  // plane hooks
  const { t } = useTranslation();
  // states
  const [isExpanded, setIsExpanded] = useState(true);
  // store hooks
  const { currentProjectActiveCycleId, currentProjectActiveCycleIds } = useCycle();
  const { getProjectById } = useProject();

  const projectDetails = getProjectById(projectId);
  const parallelCyclesEnabled = !!projectDetails?.parallel_cycles;

  const activeCycleIds: string[] = propsCycleId
    ? [propsCycleId]
    : parallelCyclesEnabled
      ? currentProjectActiveCycleIds
      : currentProjectActiveCycleId
        ? [currentProjectActiveCycleId]
        : [];

  const activeCycleResolvedPath = resolvedTheme === "light" ? lightActiveCycleAsset : darkActiveCycleAsset;

  if (activeCycleIds.length === 0) {
    return (
      <EmptyStateDetailed
        assetKey="cycle"
        title={t("project_cycles.empty_state.active.title")}
        description={t("project_cycles.empty_state.active.description")}
        rootClassName="py-10 h-auto"
      />
    );
  }

  return (
    <>
      {showHeader ? (
        <Collapsible.Root open={isExpanded} onOpenChange={setIsExpanded} className="flex flex-shrink-0 flex-col">
          <Collapsible.Trigger className="sticky top-0 z-[2] w-full flex-shrink-0 cursor-pointer border-b border-subtle bg-layer-1">
            <CycleListGroupHeader
              title={t("project_cycles.active_cycle.label")}
              type="current"
              isExpanded={isExpanded}
            />
          </Collapsible.Trigger>
          <Collapsible.Panel>
            <div className="flex flex-col gap-6">
              {activeCycleIds.map((id) => (
                <ActiveCycleItemWrapper
                  key={id}
                  workspaceSlug={workspaceSlug}
                  projectId={projectId}
                  cycleId={id}
                  activeCycleResolvedPath={activeCycleResolvedPath}
                />
              ))}
            </div>
          </Collapsible.Panel>
        </Collapsible.Root>
      ) : (
        <div className="flex flex-col gap-6">
          {activeCycleIds.map((id) => (
            <ActiveCycleItemWrapper
              key={id}
              workspaceSlug={workspaceSlug}
              projectId={projectId}
              cycleId={id}
              activeCycleResolvedPath={activeCycleResolvedPath}
            />
          ))}
        </div>
      )}
    </>
  );
});
