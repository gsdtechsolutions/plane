/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { action, makeObservable, runInAction } from "mobx";
// helpers
import { isEqual, concat, get, indexOf, isEmpty, orderBy, pull, set, uniq, update, clone } from "lodash-es";
// base class
import type { TLoader, IssuePaginationOptions, TIssuesResponse, ViewFlags, TBulkOperationsPayload } from "@plane/types";
// services
// types
import type { IBaseIssuesStore } from "../helpers/base-issues.store";
import { BaseIssuesStore } from "../helpers/base-issues.store";
import type { IIssueRootStore } from "../root.store";
import type { IArchivedIssuesFilter } from "./filter.store";

export interface IArchivedIssues extends IBaseIssuesStore {
  // observable
  viewFlags: ViewFlags;
  // actions
  fetchIssues: (
    workspaceSlug: string,
    projectId: string,
    loadType: TLoader,
    option: IssuePaginationOptions
  ) => Promise<TIssuesResponse | undefined>;
  fetchIssuesWithExistingPagination: (
    workspaceSlug: string,
    projectId: string,
    loadType: TLoader
  ) => Promise<TIssuesResponse | undefined>;
  fetchNextIssues: (
    workspaceSlug: string,
    projectId: string,
    groupId?: string,
    subGroupId?: string
  ) => Promise<TIssuesResponse | undefined>;

  restoreIssue: (workspaceSlug: string, projectId: string, issueId: string) => Promise<void>;
  removeBulkIssues: (workspaceSlug: string, projectId: string, issueIds: string[]) => Promise<void>;
  bulkUpdateProperties: (workspaceSlug: string, projectId: string, data: TBulkOperationsPayload) => Promise<void>;

  updateIssue: undefined;
  archiveIssue: undefined;
  archiveBulkIssues: undefined;
  quickAddIssue: undefined;
}

export class ArchivedIssues extends BaseIssuesStore implements IArchivedIssues {
  // filter store
  issueFilterStore: IArchivedIssuesFilter;

  //viewData
  viewFlags = {
    enableQuickAdd: false,
    enableIssueCreation: false,
    enableInlineEditing: true,
  };

  constructor(_rootStore: IIssueRootStore, issueFilterStore: IArchivedIssuesFilter) {
    super(_rootStore, issueFilterStore, true);
    makeObservable(this, {
      // action
      fetchIssues: action,
      fetchNextIssues: action,
      fetchIssuesWithExistingPagination: action,

      restoreIssue: action,
    });
    // filter store
    this.issueFilterStore = issueFilterStore;
  }

  /**
   * Fetches the project details
   * @param workspaceSlug
   * @param projectId
   */
  fetchParentStats = async (workspaceSlug: string, projectId?: string) => {
    projectId && this.rootIssueStore.rootStore.projectRoot.project.fetchProjectDetails(workspaceSlug, projectId);
  };

  /** */
  updateParentStats = () => {};

  /**
   * This method is called to fetch the first issues of pagination
   * @param workspaceSlug
   * @param projectId
   * @param loadType
   * @param options
   * @returns
   */
  fetchIssues = async (
    workspaceSlug: string,
    projectId: string,
    loadType: TLoader = "init-loader",
    options: IssuePaginationOptions,
    isExistingPaginationOptions: boolean = false
  ) => {
    try {
      // set loader and clear store
      runInAction(() => {
        this.setLoader(loadType);
      });
      this.clear(!isExistingPaginationOptions);

      // get params from pagination options
      const params = this.issueFilterStore?.getFilterParams(options, projectId, undefined, undefined, undefined);
      // call the fetch issues API with the params
      const response = await this.issueArchiveService.getArchivedIssues(workspaceSlug, projectId, params, {
        signal: this.controller.signal,
      });

      // after fetching issues, call the base method to process the response further
      this.onfetchIssues(response, options, workspaceSlug, projectId, undefined, !isExistingPaginationOptions);
      return response;
    } catch (error) {
      // set loader to undefined if errored out
      this.setLoader(undefined);
      throw error;
    }
  };

  /**
   * This method is called subsequent pages of pagination
   * if groupId/subgroupId is provided, only that specific group's next page is fetched
   * else all the groups' next page is fetched
   * @param workspaceSlug
   * @param projectId
   * @param groupId
   * @param subGroupId
   * @returns
   */
  fetchNextIssues = async (workspaceSlug: string, projectId: string, groupId?: string, subGroupId?: string) => {
    const cursorObject = this.getPaginationData(groupId, subGroupId);
    // if there are no pagination options and the next page results do not exist the return
    if (!this.paginationOptions || (cursorObject && !cursorObject?.nextPageResults)) return;
    try {
      // set Loader
      this.setLoader("pagination", groupId, subGroupId);

      // get params from stored pagination options
      const params = this.issueFilterStore?.getFilterParams(
        this.paginationOptions,
        projectId,
        this.getNextCursor(groupId, subGroupId),
        groupId,
        subGroupId
      );
      // call the fetch issues API with the params for next page in issues
      const response = await this.issueArchiveService.getArchivedIssues(workspaceSlug, projectId, params);

      // after the next page of issues are fetched, call the base method to process the response
      this.onfetchNexIssues(response, groupId, subGroupId);
      return response;
    } catch (error) {
      // set Loader as undefined if errored out
      this.setLoader(undefined, groupId, subGroupId);
      throw error;
    }
  };

  /**
   * This Method exists to fetch the first page of the issues with the existing stored pagination
   * This is useful for refetching when filters, groupBy, orderBy etc changes
   * @param workspaceSlug
   * @param projectId
   * @param loadType
   * @returns
   */
  fetchIssuesWithExistingPagination = async (
    workspaceSlug: string,
    projectId: string,
    loadType: TLoader = "mutation"
  ) => {
    if (!this.paginationOptions) return;
    return await this.fetchIssues(workspaceSlug, projectId, loadType, this.paginationOptions, true);
  };

  /**
   * Restored the current issue from the archived issue
   * @param workspaceSlug
   * @param projectId
   * @param issueId
   * @returns
   */
  restoreIssue = async (workspaceSlug: string, projectId: string, issueId: string) => {
    // call API to restore the issue
    const response = await this.issueArchiveService.restoreIssue(workspaceSlug, projectId, issueId);

    // update the store and remove from the archived issues list once restored
    runInAction(() => {
      this.rootIssueStore.issues.updateIssue(issueId, {
        archived_at: null,
      });
      this.removeIssueFromList(issueId);
    });

    return response;
  };

  // Setting them as undefined as they can not performed on Archived issues
  updateIssue = undefined;
  archiveIssue = undefined;
  archiveBulkIssues = undefined;
  quickAddIssue = undefined;

  /**
   * @description Fork Custom Override: Bulk update properties of selected issues.
   * Unlike the CE base implementation (which APPENDS array properties to existing values), this
   * override REPLACES array-valued properties (labels, assignees, modules) with the submitted
   * list, matching the fork's `bulk-operation-issues` backend semantics so the store state
   * mirrors the server after a bulk edit from the bulk-operations action bar.
   * @param {TBulkOperationsPayload} data
   */
  bulkUpdateProperties = async (workspaceSlug: string, projectId: string, data: TBulkOperationsPayload) => {
    const issueIds = data.issue_ids;
    // make request to update issue properties
    await this.issueService.bulkOperations(workspaceSlug, projectId, data);
    // update issues in the store
    runInAction(() => {
      issueIds.forEach((issueId) => {
        const issueBeforeUpdate = clone(this.rootIssueStore.issues.getIssueById(issueId));
        if (!issueBeforeUpdate) throw new Error("Work item not found");
        Object.keys(data.properties).forEach((key) => {
          const property = key as keyof TBulkOperationsPayload["properties"];
          const propertyValue = data.properties[property];
          // update root issue map properties
          if (Array.isArray(propertyValue)) {
            // Fork: replace the existing array with the submitted value
            this.rootIssueStore.issues.updateIssue(issueId, {
              [property]: propertyValue,
            });
          } else {
            // if property value is not an array, simply update the value
            this.rootIssueStore.issues.updateIssue(issueId, {
              [property]: propertyValue,
            });
          }
        });
        const issueDetails = this.rootIssueStore.issues.getIssueById(issueId);
        this.updateIssueList(issueDetails, issueBeforeUpdate);
      });
    });
  };
}
