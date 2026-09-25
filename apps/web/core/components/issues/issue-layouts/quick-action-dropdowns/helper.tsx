/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useMemo } from "react";
import {
  ArchiveOutline,
  CloseCircleOutline,
  CopyOutline,
  DeleteOutline,
  EditOutline,
  LinkOutline,
  NewTabOutline,
  RestoreOutline,
} from "@makeplane/propel/icons";
// plane imports
import { useTranslation } from "@plane/i18n";
import { setToast } from "@plane/blocks/toast";
import type { EIssuesStoreType, TIssue } from "@plane/types";
import type { TContextMenuItem } from "@plane/blocks/context-menu";
import { copyUrlToClipboard, generateWorkItemLink, copyTextToClipboard, htmlToPlainText } from "@plane/utils";
import { IssueService } from "@/services/issue";
import { createCopyMenuWithDuplication } from "./copy-menu-helper";

/**
 * The quick-action menus sit inside clickable rows (ControlLink anchors, row `onClick`s). The legacy
 * menu's root swallowed clicks for them; Propel's `Menu` renders no element, so the trigger carries
 * the guard (click + Enter/Space) and the portalled popup stops item clicks from bubbling up the React
 * tree to the row. Base UI still runs its own trigger handler, so the menu opens as before.
 */
export const quickActionTriggerGuard = {
  onClick: (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
  },
  onKeyDown: (e: React.KeyboardEvent) => {
    if (e.key === "Enter" || e.key === " ") e.stopPropagation();
  },
};

/**
 * `MenuTrigger` defaults to `nativeButton`, so a caller's `customActionButton` that isn't a real
 * `<button>` (e.g. a `<div>`) would get no role/tabIndex and trip Base UI's native-button check.
 * Returns the `nativeButton` flag for the rendered trigger: true for the IconButton fallback and
 * `<button>` elements, false otherwise so Base UI adds `role="button"` + `tabIndex` + key handling.
 */
export const isNativeQuickActionTrigger = (customActionButton: React.ReactElement | undefined) =>
  !customActionButton || customActionButton.type === "button";

export const stopQuickActionPropagation = (e: React.SyntheticEvent) => {
  e.stopPropagation();
};

// Generic helper function to handle optional function calls gracefully
// Overload for functions without parameters
export function handleOptionalAction(
  optionalFn: (() => void) | (() => Promise<void>) | undefined,
  actionName: string
): void;

// Overload for functions with one parameter
export function handleOptionalAction<T>(
  optionalFn: ((param: T) => void) | ((param: T) => Promise<void>) | undefined,
  actionName: string,
  param: T
): void;

// Implementation
export function handleOptionalAction<T>(
  optionalFn: (() => void) | (() => Promise<void>) | ((param: T) => void) | ((param: T) => Promise<void>) | undefined,
  actionName: string,
  param?: T
): void {
  if (optionalFn) {
    if (param !== undefined) {
      (optionalFn as (param: T) => void | Promise<void>)(param);
    } else {
      (optionalFn as () => void | Promise<void>)();
    }
  } else {
    setToast({
      type: "error",
      title: "Action not available",
      message: `${actionName} action is not implemented.`,
    });
  }
}

export interface MenuItemFactoryProps {
  issue: TIssue;
  workspaceSlug?: string;
  projectIdentifier?: string;
  activeLayout?: string;
  isEditingAllowed: boolean;
  isArchivingAllowed?: boolean;
  isDeletingAllowed: boolean;
  isRestoringAllowed?: boolean;
  isInArchivableGroup?: boolean;
  issueTypeDetail?: { is_active?: boolean };
  // Action handlers
  setIssueToEdit: (issue: TIssue | undefined) => void;
  setCreateUpdateIssueModal: (open: boolean) => void;
  setDeleteIssueModal: (open: boolean) => void;
  setArchiveIssueModal?: (open: boolean) => void;
  setDuplicateWorkItemModal?: (open: boolean) => void;
  handleRemoveFromView?: () => void;
  handleRestore?: () => Promise<void>;
  // External handlers
  handleDelete?: () => Promise<void>;
  handleUpdate?: (data: TIssue) => Promise<void>;
  handleArchive?: () => Promise<void>;
  // Context-specific data
  cycleId?: string;
  moduleId?: string;
  storeType?: EIssuesStoreType;
}

// Common action handlers hook
export const useIssueActionHandlers = (props: MenuItemFactoryProps) => {
  const { issue, workspaceSlug, projectIdentifier, handleRestore } = props;

  const workItemLink = useMemo(
    () =>
      generateWorkItemLink({
        workspaceSlug,
        projectId: issue?.project_id,
        issueId: issue?.id,
        projectIdentifier,
        sequenceId: issue?.sequence_id,
      }),
    [workspaceSlug, projectIdentifier, issue]
  );

  const handleCopyIssueLink = () =>
    copyUrlToClipboard(workItemLink).then(() =>
      setToast({
        type: "success",
        title: "Link copied",
        message: "Work item link copied to clipboard",
      })
    );

  const handleOpenInNewTab = () => window.open(workItemLink, "_blank");

  const handleIssueRestore = async () => {
    if (!handleRestore) {
      handleOptionalAction(handleRestore, "Restore");
      return;
    }
    await handleRestore()
      // oxlint-disable-next-line promise/always-return
      .then(() => {
        setToast({
          type: "success",
          title: "Restore success",
          message: "Your work item can be found in project work items.",
        });
      })
      .catch(() => {
        setToast({
          type: "error",
          title: "Error!",
          message: "Work item could not be restored. Please try again.",
        });
      });
  };

  /**
   * @description Fork Helper: Resolves the issue description HTML and converts it to clean formatted plain text.
   * If the description is not loaded in the lightweight issue object, fetches the full issue from the API.
   * @returns {Promise<string>} Clean plain text description with preserved line breaks.
   */
  const getOrFetchDescription = async (): Promise<string> => {
    if (issue?.description_html !== undefined) {
      return htmlToPlainText(issue.description_html);
    }
    if (!workspaceSlug || !issue?.project_id || !issue?.id) {
      return "";
    }
    try {
      const issueService = new IssueService();
      const fullIssue = await issueService.retrieve(workspaceSlug, issue.project_id, issue.id);
      return htmlToPlainText(fullIssue?.description_html || "");
    } catch (e) {
      console.error("Failed to fetch issue description", e);
      return "";
    }
  };

  /**
   * @description Fork Handler: Copies the issue title to the clipboard.
   */
  const handleCopyIssueTitle = () =>
    copyTextToClipboard(issue?.name || "").then(() =>
      setToast({
        type: "success",
        title: "Title copied",
        message: "Work item title copied to clipboard",
      })
    );

  /**
   * @description Fork Handler: Copies the issue description (as plain text) to the clipboard.
   */
  const handleCopyIssueDescription = async () => {
    const descriptionText = await getOrFetchDescription();
    if (descriptionText === "") {
      setToast({
        type: "error",
        title: "No description",
        message: "This work item has no description.",
      });
      return;
    }
    return copyTextToClipboard(descriptionText).then(() =>
      setToast({
        type: "success",
        title: "Description copied",
        message: "Work item description copied to clipboard",
      })
    );
  };

  /**
   * @description Fork Handler: Smart copy for issue details.
   * Copies title on the first line and formatted description on following lines to clipboard,
   * or just the title if no description exists.
   * @returns {Promise<void>}
   */
  const handleCopyIssueDetails = async () => {
    const titleText = (issue?.name || "").trim();
    const descriptionText = await getOrFetchDescription();
    const textToCopy = descriptionText ? `${titleText}\n\n${descriptionText}` : titleText;
    return copyTextToClipboard(textToCopy).then(() =>
      setToast({
        type: "success",
        title: descriptionText ? "Title & description copied" : "Title copied",
        message: descriptionText
          ? "Work item title & description copied to clipboard"
          : "Work item title copied to clipboard",
      })
    );
  };

  return {
    workItemLink,
    handleCopyIssueLink,
    handleOpenInNewTab,
    handleIssueRestore,
    handleCopyIssueTitle,
    handleCopyIssueDescription,
    handleCopyIssueDetails,
  };
};

export const useMenuItemFactory = (props: MenuItemFactoryProps) => {
  const { t } = useTranslation();
  const actionHandlers = useIssueActionHandlers(props);

  const {
    issue,
    activeLayout = "",
    isEditingAllowed,
    isArchivingAllowed = false,
    isDeletingAllowed,
    isRestoringAllowed = false,
    isInArchivableGroup = false,
    issueTypeDetail,
    setIssueToEdit,
    setCreateUpdateIssueModal,
    setDeleteIssueModal,
    setArchiveIssueModal,
    setDuplicateWorkItemModal,
    handleRemoveFromView,
  } = props;

  const createEditMenuItem = (customEditAction?: () => void): TContextMenuItem => ({
    key: "edit",
    title: t("common.actions.edit"),
    icon: EditOutline,
    action:
      customEditAction ||
      (() => {
        setIssueToEdit(issue);
        setCreateUpdateIssueModal(true);
      }),
    shouldRender: isEditingAllowed,
  });

  const createCopyMenuItem = (workspaceSlug?: string): TContextMenuItem => {
    const baseItem = {
      key: "make-a-copy",
      title: t("common.actions.make_a_copy"),
      icon: CopyOutline,
      action: () => {
        setCreateUpdateIssueModal(true);
      },
      shouldRender: isEditingAllowed && (issueTypeDetail?.is_active ?? true),
    };

    return createCopyMenuWithDuplication({
      baseItem,
      activeLayout,
      setCreateUpdateIssueModal,
      setDuplicateWorkItemModal,
      workspaceSlug,
    });
  };

  const createOpenInNewTabMenuItem = (): TContextMenuItem => ({
    key: "open-in-new-tab",
    title: t("common.actions.open_in_new_tab"),
    icon: NewTabOutline,
    action: actionHandlers.handleOpenInNewTab,
  });

  const createCopyLinkMenuItem = (): TContextMenuItem => ({
    key: "copy-link",
    title: t("common.actions.copy_link"),
    icon: LinkOutline,
    action: actionHandlers.handleCopyIssueLink,
  });

  /**
   * @description Fork Menu Item: Returns a smart copy details menu item.
   * Copies the title and description (if available) directly to the clipboard without a dropdown submenu.
   */
  const createCopyDetailsMenuItem = (): TContextMenuItem => ({
    key: "copy-details",
    title: t("common.actions.copy_details") || "Copy details",
    icon: CopyOutline,
    action: actionHandlers.handleCopyIssueDetails,
    shouldRender: true,
  });

  const createRemoveFromCycleMenuItem = (): TContextMenuItem => ({
    key: "remove-from-cycle",
    title: "Remove from cycle",
    icon: CloseCircleOutline,
    action: () => handleOptionalAction(handleRemoveFromView, "Remove from cycle"),
    shouldRender: isEditingAllowed,
  });

  const createRemoveFromModuleMenuItem = (): TContextMenuItem => ({
    key: "remove-from-module",
    title: "Remove from module",
    icon: CloseCircleOutline,
    action: () => handleOptionalAction(handleRemoveFromView, "Remove from module"),
    shouldRender: isEditingAllowed,
  });

  const createArchiveMenuItem = (): TContextMenuItem => ({
    key: "archive",
    title: t("common.actions.archive"),
    description: isInArchivableGroup ? undefined : t("issue.archive.description"),
    icon: ArchiveOutline,
    action: () => handleOptionalAction(setArchiveIssueModal, "Archive", true),
    disabled: !isInArchivableGroup,
    shouldRender: isArchivingAllowed,
  });

  const createRestoreMenuItem = (): TContextMenuItem => ({
    key: "restore",
    title: "Restore",
    icon: RestoreOutline,
    action: actionHandlers.handleIssueRestore,
    shouldRender: isRestoringAllowed,
  });

  const createDeleteMenuItem = (): TContextMenuItem => ({
    key: "delete",
    title: t("common.actions.delete"),
    icon: DeleteOutline,
    action: () => {
      setDeleteIssueModal(true);
    },
    shouldRender: isDeletingAllowed,
  });

  return {
    ...actionHandlers,
    createEditMenuItem,
    createCopyMenuItem,
    createOpenInNewTabMenuItem,
    createCopyLinkMenuItem,
    createCopyDetailsMenuItem,
    createRemoveFromCycleMenuItem,
    createRemoveFromModuleMenuItem,
    createArchiveMenuItem,
    createRestoreMenuItem,
    createDeleteMenuItem,
  };
};

// Predefined menu item sets for different contexts
export const useProjectIssueMenuItems = (props: MenuItemFactoryProps): TContextMenuItem[] => {
  const factory = useMenuItemFactory(props);

  return useMemo(
    () => [
      factory.createEditMenuItem(),
      factory.createCopyMenuItem(),
      factory.createOpenInNewTabMenuItem(),
      factory.createCopyLinkMenuItem(),
      factory.createCopyDetailsMenuItem(),
      factory.createArchiveMenuItem(),
      factory.createDeleteMenuItem(),
    ],
    [factory]
  );
};

export const useWorkItemDetailMenuItems = (props: MenuItemFactoryProps): TContextMenuItem[] => {
  const factory = useMenuItemFactory(props);

  return useMemo(
    () => [
      factory.createCopyMenuItem(props.workspaceSlug),
      factory.createOpenInNewTabMenuItem(),
      factory.createArchiveMenuItem(),
      factory.createRestoreMenuItem(),
      factory.createDeleteMenuItem(),
    ],
    // oxlint-disable-next-line eslint-plugin-react-hooks/exhaustive-deps
    [factory]
  );
};

export const useAllIssueMenuItems = (props: MenuItemFactoryProps): TContextMenuItem[] => {
  const factory = useMenuItemFactory(props);

  return useMemo(
    () => [
      factory.createEditMenuItem(),
      factory.createCopyMenuItem(),
      factory.createOpenInNewTabMenuItem(),
      factory.createCopyLinkMenuItem(),
      factory.createCopyDetailsMenuItem(),
      factory.createArchiveMenuItem(),
      factory.createDeleteMenuItem(),
    ],
    [factory]
  );
};

export const useCycleIssueMenuItems = (props: MenuItemFactoryProps): TContextMenuItem[] => {
  const factory = useMenuItemFactory(props);

  const customEditAction = () => {
    props.setIssueToEdit({
      ...props.issue,
      cycle_id: props.cycleId ?? null,
    });
    props.setCreateUpdateIssueModal(true);
  };

  return useMemo(
    () => [
      factory.createEditMenuItem(customEditAction),
      factory.createCopyMenuItem(),
      factory.createOpenInNewTabMenuItem(),
      factory.createCopyLinkMenuItem(),
      factory.createCopyDetailsMenuItem(),
      factory.createRemoveFromCycleMenuItem(),
      factory.createArchiveMenuItem(),
      factory.createDeleteMenuItem(),
    ],
    // oxlint-disable-next-line eslint-plugin-react-hooks/exhaustive-deps
    [factory, props.cycleId]
  );
};

export const useModuleIssueMenuItems = (props: MenuItemFactoryProps): TContextMenuItem[] => {
  const factory = useMenuItemFactory(props);

  const customEditAction = () => {
    props.setIssueToEdit({
      ...props.issue,
      module_ids: props.moduleId ? [props.moduleId] : [],
    });
    props.setCreateUpdateIssueModal(true);
  };

  return useMemo(
    () => [
      factory.createEditMenuItem(customEditAction),
      factory.createCopyMenuItem(),
      factory.createOpenInNewTabMenuItem(),
      factory.createCopyLinkMenuItem(),
      factory.createCopyDetailsMenuItem(),
      factory.createRemoveFromModuleMenuItem(),
      factory.createArchiveMenuItem(),
      factory.createDeleteMenuItem(),
    ],
    // oxlint-disable-next-line eslint-plugin-react-hooks/exhaustive-deps
    [factory, props.moduleId]
  );
};

export const useArchivedIssueMenuItems = (props: MenuItemFactoryProps): TContextMenuItem[] => {
  const factory = useMenuItemFactory(props);

  return useMemo(
    () => [
      factory.createRestoreMenuItem(),
      factory.createOpenInNewTabMenuItem(),
      factory.createCopyLinkMenuItem(),
      factory.createCopyDetailsMenuItem(),
      factory.createDeleteMenuItem(),
    ],
    [factory]
  );
};
