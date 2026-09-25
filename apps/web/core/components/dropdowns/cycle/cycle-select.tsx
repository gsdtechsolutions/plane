/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useCallback, useMemo, useState } from "react";
import { observer } from "mobx-react";
import { useParams } from "next/navigation";
// plane imports
import { CycleSelect as CycleSelectBlock } from "@plane/blocks/property-select";
import type { CycleOption } from "@plane/blocks/property-select";
import type { SelectPaginationParams, SelectTooltip, SelectTooltipOverride, SelectVariant } from "@plane/blocks/select";
import { EUserPermissionsLevel } from "@plane/constants";
import { useTranslation } from "@plane/i18n";
import type { ICycle, TCycleGroups, TPaginatedResponse } from "@plane/types";
import { EUserProjectRoles } from "@plane/types";
// hooks
import { useCycle } from "@/hooks/store/use-cycle";
import { useUserPermissions } from "@/hooks/store/user";

/** Statuses a work item can be assigned to — completed cycles are never offered. */
const DEFAULT_CYCLE_STATUSES: TCycleGroups[] = ["current", "upcoming", "draft"];

/**
 * Orca Custom: prefix for the synthetic "create cycle" option rendered inline in the dropdown.
 * Cycle ids are UUIDs, so this prefix can never collide with a real cycle id.
 */
const CREATE_OPTION_PREFIX = "__create__:";

type CycleSelectWebProps = {
  /** Project whose cycles are offered. */
  projectId: string | undefined;
  /** Selected cycle id (`null` when none). */
  value: string | null | undefined;
  /** Emits the next cycle id, or `null` when cleared. */
  onChange: (cycleId: string | null) => void;
  variant: SelectVariant;
  disabled?: boolean;
  placeholder?: string;
  onClose?: () => void;
  /** Status filter. Defaults to `["current", "upcoming", "draft"]`. */
  status?: TCycleGroups[];
  /** Adds a "No cycle" row that clears the selection (emits `null`). Defaults to `true`. */
  clearable?: boolean;
  /** Label for the clear row. Defaults to `t("cycle.no_cycle")`. */
  clearLabel?: string;
  /** Hides these cycles from the options (e.g. the cycle the work items are being moved out of). */
  excludeIds?: string[];
  className?: string;
  tooltip?: SelectTooltip;
  /** Tab order of the trigger, for forms that sequence focus explicitly (`ETabIndices`). */
  tabIndex?: number;
};

/** CE cycle statuses come off the API upper-cased; a cycle without dates has none and reads as a draft. */
const getCycleStatus = (cycle: ICycle): TCycleGroups =>
  (cycle.status?.toLowerCase() as TCycleGroups | undefined) ?? "draft";

const toOption = (cycle: ICycle): CycleOption => ({
  id: cycle.id,
  name: cycle.name,
  status: getCycleStatus(cycle),
});

/**
 * Web binding for the presentational `CycleSelect` block. Resolves the project's cycles from the cycle
 * store (fetched on first open when the project has not been loaded yet), keeps the allowed statuses,
 * filters them by the search query and maps the selected id to a `CycleOption`.
 *
 * CE change: EE reads a paginated cycle "lite" resource (and supports `scopedProjectIds`); CE serves
 * the store's list as one page. EE keeps this binding in `issues/issue-detail/cycle-select.tsx`.
 */
export const CycleSelect = observer(function CycleSelect(props: CycleSelectWebProps) {
  const {
    projectId,
    value,
    onChange,
    variant,
    disabled,
    placeholder,
    onClose,
    status,
    clearable,
    clearLabel,
    excludeIds,
    className,
    tooltip,
    tabIndex,
  } = props;
  // router params
  const { workspaceSlug } = useParams();
  // store hooks
  const { getProjectCycleIds, getCycleById, fetchAllCycles, createCycle } = useCycle();
  const { allowPermissions } = useUserPermissions();
  // translation
  const { t } = useTranslation();
  // Orca Custom: creating state for the inline cycle creation flow
  const [isCreating, setIsCreating] = useState(false);

  // Orca Custom: inline cycle creation is available to project admins/members
  const canCreateCycle =
    !disabled &&
    !!projectId &&
    !!workspaceSlug &&
    allowPermissions(
      [EUserProjectRoles.ADMIN, EUserProjectRoles.MEMBER],
      EUserPermissionsLevel.PROJECT,
      workspaceSlug.toString(),
      projectId
    );
  const resolvedTooltip = useMemo<SelectTooltipOverride | undefined>(() => {
    if (!tooltip) return undefined;
    const override = typeof tooltip === "object" ? tooltip : undefined;
    return {
      heading: override?.heading ?? t("common.cycle"),
      emptyContent: override?.emptyContent ?? t("common.none"),
    };
  }, [tooltip, t]);

  const allowedStatuses = status ?? DEFAULT_CYCLE_STATUSES;

  const getValues = useCallback(
    async ({ search }: SelectPaginationParams): Promise<TPaginatedResponse<CycleOption[]>> => {
      if (!workspaceSlug || !projectId) return { results: [] };
      let cycleIds = getProjectCycleIds(projectId);
      if (!cycleIds) {
        await fetchAllCycles(workspaceSlug.toString(), projectId);
        cycleIds = getProjectCycleIds(projectId);
      }
      const query = search?.trim().toLowerCase();
      const excluded = new Set(excludeIds);
      const statuses = new Set(allowedStatuses);
      const results = (cycleIds ?? [])
        .filter((cycleId) => !excluded.has(cycleId))
        .map((cycleId) => getCycleById(cycleId))
        .filter((cycle): cycle is ICycle => !!cycle)
        .filter((cycle) => statuses.has(getCycleStatus(cycle)))
        .filter((cycle) => !query || cycle.name.toLowerCase().includes(query))
        .map(toOption);
      // Orca Custom: inline cycle creation — when the query does not exactly match an existing
      // cycle and the user may create cycles, append a synthetic "Add to cycles" option.
      if (canCreateCycle && query) {
        const trimmedQuery = query.trim();
        const hasExactMatch = results.some((option) => option.name.trim().toLowerCase() === trimmedQuery.toLowerCase());
        if (trimmedQuery && !hasExactMatch) {
          results.push({
            id: `${CREATE_OPTION_PREFIX}${trimmedQuery}`,
            name: `+ Add "${trimmedQuery}" to cycles`,
          });
        }
      }
      return { results, next_page_results: false };
    },
    [workspaceSlug, projectId, getProjectCycleIds, fetchAllCycles, getCycleById, excludeIds, allowedStatuses, canCreateCycle]
  );

  // Resolved in the observer render (not in a memo) so MobX re-renders when the cycle loads.
  const cycle = value ? getCycleById(value) : null;
  const selected = cycle ? toOption(cycle) : null;

  /**
   * @description Orca Custom: Handles selecting an existing cycle or creating a new one inline.
   * A selection carrying the synthetic create prefix creates (or picks) a cycle with that name,
   * then emits the real cycle id. Any other id is passed through.
   */
  const handleChange = useCallback(
    async (cycleId: string) => {
      if (cycleId.startsWith(CREATE_OPTION_PREFIX)) {
        const name = cycleId.slice(CREATE_OPTION_PREFIX.length).trim();
        if (!name || !workspaceSlug || !projectId || isCreating) return;
        setIsCreating(true);
        try {
          const existingCycle = (getProjectCycleIds(projectId) ?? [])
            .map((id) => getCycleById(id))
            .find((c) => c?.name.trim().toLowerCase() === name.toLowerCase());
          const selectedId = existingCycle
            ? existingCycle.id
            : (await createCycle(workspaceSlug.toString(), projectId, { name })).id;
          onChange(selectedId);
        } catch (error) {
          console.error("Failed to create cycle inline", error);
        } finally {
          setIsCreating(false);
        }
        return;
      }
      onChange(cycleId || null);
    },
    [workspaceSlug, projectId, getProjectCycleIds, getCycleById, createCycle, onChange, isCreating]
  );

  return (
    <CycleSelectBlock
      getValues={getValues}
      value={selected}
      onChange={handleChange}
      variant={variant}
      onClose={onClose}
      disabled={disabled}
      placeholder={placeholder}
      className={className}
      clearable={clearable}
      clearLabel={clearLabel ?? t("cycle.no_cycle")}
      tooltip={resolvedTooltip}
      tabIndex={tabIndex}
    />
  );
});
