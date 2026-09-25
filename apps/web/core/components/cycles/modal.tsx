/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useState } from "react";
import { mutate } from "swr";
// types
import { setToast } from "@plane/blocks/toast";
import type { CycleDateCheckData, ICycle, TCycleTabOptions } from "@plane/types";
// ui
import { Dialog, DialogContent } from "@makeplane/propel/components/dialog";
// hooks
import { renderFormattedPayloadDate } from "@plane/utils";
import { useCycle } from "@/hooks/store/use-cycle";
import { useProject } from "@/hooks/store/use-project";
import useLocalStorage from "@/hooks/use-local-storage";
import { usePlatformOS } from "@/hooks/use-platform-os";
// services
import { CycleService } from "@/services/cycle.service";
// local imports
import { CycleForm } from "./form";

type CycleModalProps = {
  isOpen: boolean;
  handleClose: () => void;
  data?: ICycle | null;
  workspaceSlug: string;
  projectId: string;
};

// services
const cycleService = new CycleService();

/**
 * @description Modal component to create or update cycles.
 * Custom behavior (orca port): If `parallel_cycles` is enabled for the active project, the modal
 * allows overlapping dates by bypassing the date overlap validation when submitting the form.
 */
export function CycleCreateUpdateModal(props: CycleModalProps) {
  const { isOpen, handleClose, data, workspaceSlug, projectId } = props;
  // states
  const [activeProject, setActiveProject] = useState<string | null>(null);
  // store hooks
  const { workspaceProjectIds, getProjectById } = useProject();
  const { createCycle, updateCycleDetails } = useCycle();
  const { isMobile } = usePlatformOS();

  const { setValue: setCycleTab } = useLocalStorage<TCycleTabOptions>("cycle_tab", "active");

  // Orca Custom Override: read the project's parallel_cycles sidecar setting (exposed by the API
  // on the project serializer) to decide whether date overlap checks should be bypassed.
  const projectDetails = getProjectById(projectId);
  const parallelCyclesEnabled = !!projectDetails?.parallel_cycles;

  const handleCreateCycle = async (payload: Partial<ICycle>) => {
    if (!workspaceSlug || !projectId) return;

    const selectedProjectId = payload.project_id ?? projectId.toString();
    await createCycle(workspaceSlug, selectedProjectId, payload)
      .then((_res) => {
        // mutate when the current cycle creation is active
        if (payload.start_date && payload.end_date) {
          const currentDate = new Date();
          const cycleStartDate = new Date(payload.start_date);
          const cycleEndDate = new Date(payload.end_date);
          if (currentDate >= cycleStartDate && currentDate <= cycleEndDate) {
            mutate(`PROJECT_ACTIVE_CYCLE_${selectedProjectId}`);
          }
        }

        setToast({
          type: "success",
          title: "Success!",
          message: "Cycle created successfully.",
        });
      })
      .catch((err) => {
        setToast({
          type: "error",
          title: "Error!",
          message: err?.detail ?? "Error in creating cycle. Please try again.",
        });
      });
  };

  const handleUpdateCycle = async (cycleId: string, payload: Partial<ICycle>) => {
    if (!workspaceSlug || !projectId) return;

    const selectedProjectId = payload.project_id ?? projectId.toString();
    await updateCycleDetails(workspaceSlug, selectedProjectId, cycleId, payload)
      .then((_res) => {
        setToast({
          type: "success",
          title: "Success!",
          message: "Cycle updated successfully.",
        });
      })
      .catch((err) => {
        setToast({
          type: "error",
          title: "Error!",
          message: err?.detail ?? "Error in updating cycle. Please try again.",
        });
      });
  };

  const dateChecker = async (projectId: string, payload: CycleDateCheckData) => {
    let status = false;

    await cycleService.cycleDateCheck(workspaceSlug, projectId, payload).then((res) => {
      status = res.status;
    });

    return status;
  };

  const handleFormSubmit = async (formData: Partial<ICycle>) => {
    if (!workspaceSlug || !projectId) return;

    const payload: Partial<ICycle> = {
      ...formData,
      start_date: renderFormattedPayloadDate(formData.start_date) ?? null,
      end_date: renderFormattedPayloadDate(formData.end_date) ?? null,
    };

    let isDateValid: boolean = true;

    // Orca Custom Override: Bypass date overlap checks if parallel cycles are enabled for this project
    if (payload.start_date && payload.end_date && !parallelCyclesEnabled) {
      if (data?.id) {
        // Update existing cycle - only check dates if they've changed
        const originalStartDate = renderFormattedPayloadDate(data.start_date) ?? null;
        const originalEndDate = renderFormattedPayloadDate(data.end_date) ?? null;
        const hasDateChanged = payload.start_date !== originalStartDate || payload.end_date !== originalEndDate;

        if (hasDateChanged) {
          isDateValid = await dateChecker(projectId, {
            start_date: payload.start_date,
            end_date: payload.end_date,
            cycle_id: data.id,
          });
        }
      } else {
        // Create new cycle - always check dates
        isDateValid = await dateChecker(projectId, {
          start_date: payload.start_date,
          end_date: payload.end_date,
        });
      }
    }

    if (isDateValid) {
      if (data?.id) {
        // Orca Custom Override: strip unchanged dates from the update payload so that sending an
        // unchanged end_date does not reset the manually-completed flag tracked by the API.
        const originalStartDate = renderFormattedPayloadDate(data.start_date) ?? null;
        const originalEndDate = renderFormattedPayloadDate(data.end_date) ?? null;
        const updatePayload: Partial<ICycle> = { ...payload };
        if (payload.start_date === originalStartDate) {
          delete updatePayload.start_date;
        }
        if (payload.end_date === originalEndDate) {
          delete updatePayload.end_date;
        }
        await handleUpdateCycle(data.id, updatePayload);
      } else {
        await handleCreateCycle(payload);
        setCycleTab("all");
      }
      handleClose();
    } else
      setToast({
        type: "error",
        title: "Error!",
        message: "You already have a cycle on the given dates, if you want to create a draft cycle, remove the dates.",
      });
  };

  useEffect(() => {
    // if modal is closed, reset active project to null
    // and return to avoid activeProject being set to some other project
    if (!isOpen) {
      setActiveProject(null);
      return;
    }

    // if data is present, set active project to the project of the
    // issue. This has more priority than the project in the url.
    if (data && data.project_id) {
      setActiveProject(data.project_id);
      return;
    }

    // if data is not present, set active project to the project
    // in the url. This has the least priority.
    if (workspaceProjectIds && workspaceProjectIds.length > 0 && !activeProject)
      setActiveProject(projectId ?? workspaceProjectIds?.[0] ?? null);
  }, [activeProject, data, projectId, workspaceProjectIds, isOpen]);

  return (
    <Dialog
      open={isOpen}
      // The legacy modal shell had no `handleClose`, so an outside press never dismissed it and
      // Escape came from `useKeypress`; Base UI's own Escape now reaches the same handler.
      disablePointerDismissal
      onOpenChange={(open) => {
        if (!open) handleClose();
      }}
    >
      <DialogContent size="md">
        <CycleForm
          handleFormSubmit={handleFormSubmit}
          handleClose={handleClose}
          status={!!data}
          projectId={activeProject ?? ""}
          setActiveProject={setActiveProject}
          data={data}
          isMobile={isMobile}
        />
      </DialogContent>
    </Dialog>
  );
}
