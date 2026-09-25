/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useState } from "react";
import { observer } from "mobx-react";
import { useParams } from "next/navigation";
// plane imports
import { setToast } from "@plane/blocks/toast";
import { ConfirmDialog } from "@plane/blocks/dialog";
// hooks
import { useIssuesStore } from "@/hooks/use-issue-layout-store";

type Props = {
  isOpen: boolean;
  issueIds: string[];
  onClose: () => void;
  onSuccess: () => void;
};

/**
 * @description Fork customization: confirmation modal for bulk-archiving the selected work items.
 */
export const BulkArchiveConfirmModal = observer(function BulkArchiveConfirmModal(props: Props) {
  const { isOpen, issueIds, onClose, onSuccess } = props;
  // router
  const { workspaceSlug, projectId } = useParams();
  // store
  // NOTE: the CE base store implements `bulkArchiveIssues` but does not declare it on its
  // interface; narrow through a local alias instead of editing the shared base store file.
  const { issues } = useIssuesStore();
  const bulkArchiveIssues = (
    issues as unknown as { bulkArchiveIssues: (w: string, p: string, ids: string[]) => Promise<void> }
  ).bulkArchiveIssues;
  // state
  const [isArchiving, setIsArchiving] = useState(false);

  const handleArchive = async () => {
    if (!workspaceSlug || !projectId || issueIds.length === 0) return;
    setIsArchiving(true);
    try {
      await bulkArchiveIssues(workspaceSlug.toString(), projectId.toString(), issueIds);
      setToast({
        type: "success",
        title: "Archived!",
        message: `${issueIds.length} work item${issueIds.length > 1 ? "s" : ""} archived successfully.`,
      });
      onSuccess();
      onClose();
    } catch {
      setToast({
        type: "error",
        title: "Error",
        message: "Something went wrong while archiving. Please try again.",
      });
    } finally {
      setIsArchiving(false);
    }
  };

  return (
    <ConfirmDialog
      isOpen={isOpen}
      handleClose={onClose}
      handleSubmit={handleArchive}
      isSubmitting={isArchiving}
      title="Archive work items"
      variant="primary"
      primaryButtonText={{
        loading: "Archiving...",
        default: `Archive ${issueIds.length} item${issueIds.length > 1 ? "s" : ""}`,
      }}
      content={
        <>
          Are you sure you want to archive{" "}
          <span className="font-medium break-words text-primary">
            {issueIds.length} work item{issueIds.length > 1 ? "s" : ""}
          </span>
          ? You can view and restore them from the project archives later.
        </>
      }
    />
  );
});
