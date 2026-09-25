/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// ORCA PORT: workspace-level "Project states" settings — manage the shared state
// library and toggle whether new projects adopt it by default.

import { useEffect, useMemo, useState } from "react";
import { observer } from "mobx-react";
import { useParams } from "react-router";
// plane imports
import { Switch } from "@makeplane/propel/components/switch";
import { ConfirmDialog } from "@plane/blocks/dialog";
import { setToast } from "@plane/blocks/toast";
import { useTranslation } from "@plane/i18n";
// components
import { PageHead } from "@/components/core/page-title";
import { SettingsContentWrapper } from "@/components/settings/content-wrapper";
import { GroupList } from "@/components/project-states";
// hooks
import { useCustomProjectState } from "@/hooks/store/use-custom-project-state";
// local imports
import { ProjectStatesWorkspaceSettingsHeader } from "./header";

const ProjectStatesPage = observer(function ProjectStatesPage() {
  const { workspaceSlug } = useParams();
  const store = useCustomProjectState();
  const { t } = useTranslation();

  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    if (!workspaceSlug) return;
    Promise.all([store.fetchSettings(workspaceSlug.toString()), store.fetchStates(workspaceSlug.toString())]).finally(
      () => {
        setIsLoading(false);
      }
    );
  }, [workspaceSlug, store]);

  // Toggle settings states
  const [isConfirmationModalOpen, setIsConfirmationModalOpen] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const handleToggle = () => {
    setIsConfirmationModalOpen(true);
  };

  const handleConfirmToggle = async () => {
    if (!workspaceSlug || !store.settings) return;
    setIsSubmitting(true);
    const nextValue = !store.settings.is_enabled;
    try {
      await store.updateSettings(workspaceSlug.toString(), {
        is_enabled: nextValue,
      });
      setToast({
        type: "success",
        title: "Success",
        message: `Project States feature ${nextValue ? "enabled" : "disabled"} successfully.`,
      });
    } catch (_e) {
      setToast({
        type: "error",
        title: "Error",
        message: "Failed to update project state settings.",
      });
    } finally {
      setIsSubmitting(false);
      setIsConfirmationModalOpen(false);
    }
  };

  // Map the workspace-level states onto the shape the shared states UI expects.
  const mappedStates = useMemo(() => {
    return (
      store.states?.map((st) => ({
        id: st.id,
        name: st.name,
        description: st.description || "",
        color: st.color,
        group: st.group as any,
        default: st.default,
        sequence: st.sequence,
        project_id: "",
        workspace_id: "",
        order: st.sequence,
      })) || []
    );
  }, [store.states]);

  const groupedStates = useMemo(() => {
    const groups: Record<string, any[]> = {
      backlog: [],
      unstarted: [],
      started: [],
      completed: [],
      cancelled: [],
    };
    mappedStates.forEach((state) => {
      if (groups[state.group]) {
        groups[state.group].push(state);
      }
    });
    return groups;
  }, [mappedStates]);

  const stateOperationsCallbacks = useMemo(
    () => ({
      createState: async (data: any) => {
        if (!workspaceSlug) throw new Error("Workspace slug is required");
        const res = await store.createState(workspaceSlug, {
          name: data.name,
          description: data.description || "",
          color: data.color,
          group: data.group,
          default: data.default || false,
        });
        setToast({
          type: "success",
          title: "Success",
          message: "State created successfully.",
        });
        return {
          id: res.id,
          name: res.name,
          description: res.description || "",
          color: res.color,
          group: res.group as any,
          default: res.default,
          sequence: res.sequence,
          project_id: "",
          workspace_id: "",
          order: res.sequence,
        };
      },
      updateState: async (stateId: string, data: any) => {
        if (!workspaceSlug) throw new Error("Workspace slug is required");
        const res = await store.updateState(workspaceSlug, stateId, {
          name: data.name,
          description: data.description || "",
          color: data.color,
          group: data.group,
          default: data.default || false,
        });
        setToast({
          type: "success",
          title: "Success",
          message: "State updated successfully.",
        });
        return {
          id: res.id,
          name: res.name,
          description: res.description || "",
          color: res.color,
          group: res.group as any,
          default: res.default,
          sequence: res.sequence,
          project_id: "",
          workspace_id: "",
          order: res.sequence,
        };
      },
      deleteState: async (stateId: string) => {
        if (!workspaceSlug) throw new Error("Workspace slug is required");
        await store.deleteState(workspaceSlug, stateId);
        setToast({
          type: "success",
          title: "Success",
          message: "State deleted successfully.",
        });
      },
      markStateAsDefault: async (stateId: string) => {
        if (!workspaceSlug) throw new Error("Workspace slug is required");
        await store.updateState(workspaceSlug, stateId, { default: true });
        setToast({
          type: "success",
          title: "Success",
          message: "Default state updated successfully.",
        });
      },
      moveStatePosition: async (stateId: string, data: any) => {
        if (!workspaceSlug) throw new Error("Workspace slug is required");
        await store.updateState(workspaceSlug, stateId, {
          sequence: data.sequence,
        });
      },
    }),
    [workspaceSlug, store]
  );

  if (isLoading) {
    return (
      <SettingsContentWrapper header={<ProjectStatesWorkspaceSettingsHeader />}>
        <div className="text-custom-text-300 flex h-full w-full items-center justify-center py-20">
          Loading settings...
        </div>
      </SettingsContentWrapper>
    );
  }

  return (
    <SettingsContentWrapper header={<ProjectStatesWorkspaceSettingsHeader />}>
      <PageHead title="Workspace Project States" />
      <div className="flex max-w-4xl flex-col gap-6 p-6">
        <div className="flex items-center justify-between border-b border-subtle pb-6">
          <div>
            <h3 className="text-xl text-custom-text-100 font-medium">
              {(() => {
                const title = t("workspace_settings.settings.project_states.title");
                return !title || title === "workspace_settings.settings.project_states.title"
                  ? "Project States"
                  : title;
              })()}
            </h3>
            <p className="text-sm text-custom-text-300">
              Enable project states to classify and track project lifecycles consistently across the workspace.
            </p>
          </div>
          <Switch
            size="lg"
            checked={store.settings?.is_enabled || false}
            onCheckedChange={(checked) => (checked ? handleToggle() : handleToggle())}
            disabled={isSubmitting}
          />
        </div>

        {store.settings?.is_enabled && (
          <div className="flex flex-col gap-4">
            <div className="flex items-center justify-between">
              <h4 className="text-lg text-custom-text-200 font-medium font-semibold">Workspace Project States</h4>
            </div>

            <div className="mt-2">
              <GroupList groupedStates={groupedStates} stateOperationsCallbacks={stateOperationsCallbacks} isEditable />
            </div>
          </div>
        )}
      </div>
      {isConfirmationModalOpen && (
        <ConfirmDialog
          isOpen={isConfirmationModalOpen}
          handleClose={() => setIsConfirmationModalOpen(false)}
          handleSubmit={handleConfirmToggle}
          isSubmitting={isSubmitting}
          title="Confirm Toggle Project States"
          content={`Are you sure you want to ${store.settings?.is_enabled ? "disable" : "enable"} workspace project states? This will change how states are managed across all projects. Work items in projects might have their states aligned or modified.`}
          variant="primary"
          primaryButtonText={{
            loading: "Updating...",
            default: "Confirm",
          }}
        />
      )}
    </SettingsContentWrapper>
  );
});

export default ProjectStatesPage;
