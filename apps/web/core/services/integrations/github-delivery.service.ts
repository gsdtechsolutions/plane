/** Copyright (c) 2023-present Plane Software, Inc. and contributors. SPDX-License-Identifier: AGPL-3.0-only */
import { API_BASE_URL } from "@plane/constants";
import { APIService } from "@/services/api.service";

export type GithubConnection = {
  id: string;
  installation_id: number;
  account: string;
  host: string;
  host_display: string;
  app_slug: string | null;
  active: boolean;
};
export type GithubRepository = { id: number; full_name: string; private: boolean };
export type GithubMapping = {
  id: string;
  connection_id: string;
  project_id: string;
  repository_id: number;
  repository: string;
  private: boolean;
  active: boolean;
  sync_status: string;
  sync_error: string;
  last_synced_at: string | null;
};
export type GithubPullRequest = {
  id: string;
  repository: string;
  number: number;
  title: string;
  url: string;
  state: "open" | "closed" | "merged";
  draft: boolean;
  merged_at: string | null;
  review_state: string;
  linked_manually: boolean;
  connected: boolean;
};
export type GithubRelease = {
  id: string;
  repository: string;
  tag_name: string;
  name: string;
  url: string;
  body: string;
  draft: boolean;
  prerelease: boolean;
  published_at: string | null;
};
export type GithubStatus = {
  connect_urls: {
    setup_url: string | null;
    callback_url: string | null;
    webhook_url: string | null;
  };
  permissions: string[];
  connections: GithubConnection[];
  mappings: GithubMapping[];
};
export type GithubCommit = {
  id: string;
  sha: string;
  short_sha: string;
  message: string;
  author: string;
  committed_at: string | null;
  url: string;
  connected: boolean;
};
export type GithubTimelineEvent = {
  kind: "commit" | "pr_opened" | "pr_merged" | "pr_closed";
  at: string | null;
  title: string;
  detail: string;
  author: string;
  url: string;
  repository: string;
};
export type GithubIssueDevelopment = {
  pull_requests: GithubPullRequest[];
  commits: GithubCommit[];
  timeline: GithubTimelineEvent[];
  mention_search: { running: boolean; searched_at: string | null; error: string };
};
export type GithubDevelopment = {
  repositories: GithubMapping[];
  pull_requests: GithubPullRequest[];
  releases: GithubRelease[];
};

export function githubError(error: unknown): string {
  const value = error as { response?: { data?: { detail?: unknown; error?: unknown }; status?: number } };
  if (value?.response?.status === 403) return "You do not have permission to access this GitHub connection.";
  const detail = value?.response?.data?.detail ?? value?.response?.data?.error;
  return typeof detail === "string" ? detail : "GitHub could not complete this request. Please try again.";
}

class GithubDeliveryService extends APIService {
  constructor() {
    super(API_BASE_URL);
  }
  private workspace(slug: string) {
    return `/api/workspaces/${slug}/github-delivery/`;
  }
  private issue(slug: string, project: string, issue: string) {
    return `/api/workspaces/${slug}/projects/${project}/issues/${issue}/github-pull-requests/`;
  }
  async status(slug: string): Promise<GithubStatus> {
    return (await this.get(this.workspace(slug))).data;
  }
  async connect(
    slug: string,
    data: { account_type: "personal" | "organization" | "enterprise"; organization?: string; enterprise_url?: string }
  ): Promise<{ url: string }> {
    return (await this.post(this.workspace(slug) + "connect/", data)).data;
  }
  async disconnect(slug: string, connection: string) {
    await this.delete(this.workspace(slug) + `connections/${connection}/`);
  }
  async repositories(slug: string, connection: string): Promise<GithubRepository[]> {
    return (await this.get(this.workspace(slug) + `connections/${connection}/repositories/`)).data;
  }
  async map(
    slug: string,
    data: { connection_id: string; project_id: string; repository_id: number }
  ): Promise<GithubMapping> {
    return (await this.post(this.workspace(slug) + "mappings/", data)).data;
  }
  async unmap(slug: string, mapping: string) {
    await this.delete(this.workspace(slug) + `mappings/${mapping}/`);
  }
  async sync(slug: string, mapping: string) {
    await this.post(this.workspace(slug) + `mappings/${mapping}/`, {});
  }
  async development(slug: string, project: string): Promise<GithubDevelopment> {
    return (await this.get(`/api/workspaces/${slug}/projects/${project}/github-delivery/`)).data;
  }
  async issueDevelopment(slug: string, project: string, issue: string): Promise<GithubIssueDevelopment> {
    return (await this.get(this.issue(slug, project, issue))).data;
  }
  async link(slug: string, project: string, issue: string, url: string): Promise<GithubIssueDevelopment> {
    return (await this.post(this.issue(slug, project, issue), { url })).data;
  }
  async unlink(slug: string, project: string, issue: string, pullRequest: string) {
    await this.delete(this.issue(slug, project, issue) + `${pullRequest}/`);
  }
}
export const githubDeliveryService = new GithubDeliveryService();
