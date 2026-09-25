/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// ORCA PORT: made prop-driven so the workspace-level "Project labels" settings
// page can reuse this list with the shared workspace labels and custom callbacks.
// All props are optional — when omitted the list falls back to the project store.

import { useState, useRef } from "react";
import { observer } from "mobx-react";
import { useParams } from "next/navigation";
// plane imports
import { EUserPermissions, EUserPermissionsLevel } from "@plane/constants";
import { useTranslation } from "@plane/i18n";
import { Button } from "@makeplane/propel/components/button";
import { EmptyStateCompact } from "@plane/blocks/empty-state";
import type { IIssueLabel, IIssueLabelTree } from "@plane/types";
import { Loader } from "@plane/blocks/skeleton";
import type { TLabelOperationsCallbacks } from "@/components/labels";
import {
  CreateUpdateLabelInline,
  DeleteLabelModal,
  ProjectSettingLabelGroup,
  ProjectSettingLabelItem,
} from "@/components/labels";
// hooks
import { useLabel } from "@/hooks/store/use-label";
import { useUserPermissions } from "@/hooks/store/user";
// local imports
import { SettingsHeading } from "../settings/heading";

type TProjectSettingsLabelListProps = {
  title?: React.ReactNode;
  description?: React.ReactNode;
  labels?: IIssueLabel[];
  labelsTree?: IIssueLabelTree[];
  labelOperationsCallbacks?: TLabelOperationsCallbacks;
  onDrop?: (
    draggingLabelId: string,
    droppedParentId: string | null,
    droppedLabelId: string | undefined,
    dropAtEndOfList: boolean
  ) => void;
  isEditable?: boolean;
  handleDelete?: (label: IIssueLabel) => Promise<void>;
};

export const ProjectSettingsLabelList = observer(function ProjectSettingsLabelList(
  props: TProjectSettingsLabelListProps
) {
  const {
    title,
    description,
    labels: propLabels,
    labelsTree: propLabelsTree,
    labelOperationsCallbacks: propLabelOperationsCallbacks,
    onDrop: propOnDrop,
    isEditable: propIsEditable,
    handleDelete,
  } = props;
  // router
  const { workspaceSlug, projectId } = useParams();
  // refs
  const scrollToRef = useRef<HTMLDivElement>(null);
  // states
  const [showLabelForm, setLabelForm] = useState(false);
  const [isUpdating, setIsUpdating] = useState(false);
  const [selectDeleteLabel, setSelectDeleteLabel] = useState<IIssueLabel | null>(null);
  // plane hooks
  const { t } = useTranslation();
  // store hooks
  const {
    projectLabels: storeLabels,
    updateLabelPosition,
    projectLabelsTree: storeLabelsTree,
    createLabel,
    updateLabel,
  } = useLabel();
  const { allowPermissions } = useUserPermissions();
  // derived values
  const isEditable =
    propIsEditable !== undefined
      ? propIsEditable
      : allowPermissions([EUserPermissions.ADMIN], EUserPermissionsLevel.PROJECT);
  const projectLabels = propLabels ?? storeLabels;
  const projectLabelsTree = propLabelsTree ?? storeLabelsTree;

  const defaultLabelOperationsCallbacks: TLabelOperationsCallbacks = {
    createLabel: (data: Partial<IIssueLabel>) => createLabel(workspaceSlug?.toString(), projectId?.toString(), data),
    updateLabel: (labelId: string, data: Partial<IIssueLabel>) =>
      updateLabel(workspaceSlug?.toString(), projectId?.toString(), labelId, data),
  };
  const labelOperationsCallbacks = propLabelOperationsCallbacks ?? defaultLabelOperationsCallbacks;

  const newLabel = () => {
    setIsUpdating(false);
    setLabelForm(true);
  };

  const defaultOnDrop = (
    draggingLabelId: string,
    droppedParentId: string | null,
    droppedLabelId: string | undefined,
    dropAtEndOfList: boolean
  ) => {
    if (workspaceSlug && projectId) {
      updateLabelPosition(
        workspaceSlug?.toString(),
        projectId?.toString(),
        draggingLabelId,
        droppedParentId,
        droppedLabelId,
        dropAtEndOfList
      );
      return;
    }
  };
  const onDrop = propOnDrop ?? defaultOnDrop;

  const finalTitle = title !== undefined ? title : t("project_settings.labels.heading");
  const finalDescription = description !== undefined ? description : t("project_settings.labels.description");

  return (
    <>
      <DeleteLabelModal
        isOpen={!!selectDeleteLabel}
        data={selectDeleteLabel ?? null}
        onClose={() => setSelectDeleteLabel(null)}
        handleDelete={handleDelete}
      />
      {(finalTitle || finalDescription) && (
        <SettingsHeading
          title={finalTitle}
          description={finalDescription}
          control={
            isEditable && (
              <Button variant="primary" size="md" stretch="auto" onClick={newLabel} label={t("common.add_label")} />
            )
          }
        />
      )}
      {/* ORCA PORT: if the heading is hidden but still editable, we still need the Add Label button at the top */}
      {!finalTitle && !finalDescription && isEditable && !showLabelForm && (
        <div className="flex w-full justify-end">
          <Button variant="primary" size="md" stretch="auto" onClick={newLabel} label={t("common.add_label")} />
        </div>
      )}
      <div className="mt-6 w-full">
        {showLabelForm && (
          <div className="my-2 w-full rounded-sm border border-subtle px-3.5 py-2">
            <CreateUpdateLabelInline
              labelForm={showLabelForm}
              setLabelForm={setLabelForm}
              isUpdating={isUpdating}
              labelOperationsCallbacks={labelOperationsCallbacks}
              ref={scrollToRef}
              onClose={() => {
                setLabelForm(false);
                setIsUpdating(false);
              }}
            />
          </div>
        )}
        {projectLabels ? (
          projectLabels.length === 0 && !showLabelForm ? (
            <EmptyStateCompact
              assetKey="label"
              assetClassName="size-20"
              title={t("settings_empty_state.labels.title")}
              description={t("settings_empty_state.labels.description")}
              actions={[
                {
                  label: t("settings_empty_state.labels.cta_primary"),
                  onClick: () => {
                    newLabel();
                  },
                },
              ]}
              align="start"
              rootClassName="py-20"
            />
          ) : (
            projectLabelsTree?.map((label, index) => {
              if (label.children && label.children.length) {
                return (
                  <ProjectSettingLabelGroup
                    key={label.id}
                    label={label}
                    labelChildren={label.children || []}
                    handleLabelDelete={(lbl: IIssueLabel) => setSelectDeleteLabel(lbl)}
                    isUpdating={isUpdating}
                    setIsUpdating={setIsUpdating}
                    isLastChild={index === projectLabelsTree.length - 1}
                    onDrop={onDrop}
                    isEditable={isEditable}
                    labelOperationsCallbacks={labelOperationsCallbacks}
                  />
                );
              }
              return (
                <ProjectSettingLabelItem
                  label={label}
                  key={label.id}
                  setIsUpdating={setIsUpdating}
                  handleLabelDelete={(lbl: IIssueLabel) => setSelectDeleteLabel(lbl)}
                  isChild={false}
                  isLastChild={index === projectLabelsTree.length - 1}
                  onDrop={onDrop}
                  isEditable={isEditable}
                  labelOperationsCallbacks={labelOperationsCallbacks}
                />
              );
            })
          )
        ) : (
          !showLabelForm && (
            <Loader className="space-y-5">
              <Loader.Item height="42px" />
              <Loader.Item height="42px" />
              <Loader.Item height="42px" />
              <Loader.Item height="42px" />
            </Loader>
          )
        )}
      </div>
    </>
  );
});
