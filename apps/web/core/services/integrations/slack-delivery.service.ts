/** Copyright (c) 2023-present Plane Software, Inc. and contributors. SPDX-License-Identifier: AGPL-3.0-only */
import { API_BASE_URL } from "@plane/constants";
import { APIService } from "@/services/api.service";

export type SlackConnection = { id: string; team_id: string; team_name: string; active: boolean };
export type SlackChannel = { id: string; name: string; private: boolean; member: boolean };
export type SlackMapping = {
  id: string;
  connection_id: string;
  project_id: string;
  channel_id: string;
  channel: string;
  private: boolean;
  active: boolean;
  sync_status: string;
  sync_error: string;
  last_synced_at: string | null;
};
export type SlackLinkedMessage = {
  id: string;
  channel: string;
  channel_id: string;
  user: string;
  text: string;
  thread_ts: string | null;
  url: string | null;
  posted_at: string | null;
  deleted: boolean;
  linked_manually: boolean;
  connected: boolean;
};
export type SlackStatus = {
  configured: boolean;
  missing_settings: string[];
  configuration_error: string | null;
  callback_url: string | null;
  events_url: string | null;
  scopes: string[];
  event_subscriptions: string[];
  permissions: string[];
  connections: SlackConnection[];
  mappings: SlackMapping[];
};
export type SlackConversations = {
  channels: SlackMapping[];
  messages: SlackLinkedMessage[];
};
export type SlackSetupApp = {
  configured: boolean;
  client_id_masked: string;
  updated_at: string | null;
};
export type SlackSetupState = {
  configured: boolean;
  missing_settings: string[];
  configuration_error: string | null;
  callback_url: string | null;
  events_url: string | null;
  commands_url: string | null;
  setup_url: string | null;
  scopes: string[];
  event_subscriptions: string[];
  app: SlackSetupApp;
};
export type SlackSetupCredentials = {
  client_id: string;
  client_secret: string;
  signing_secret: string;
};

export function slackError(error: unknown): string {
  const value = error as { response?: { data?: { detail?: unknown; error?: unknown }; status?: number } };
  if (value?.response?.status === 403) return "You do not have permission to access this Slack connection.";
  const detail = value?.response?.data?.detail ?? value?.response?.data?.error;
  return typeof detail === "string" ? detail : "Slack could not complete this request. Please try again.";
}

class SlackDeliveryService extends APIService {
  constructor() {
    super(API_BASE_URL);
  }
  private workspace(slug: string) {
    return `/api/workspaces/${slug}/slack-delivery/`;
  }
  private issue(slug: string, project: string, issue: string) {
    return `/api/workspaces/${slug}/projects/${project}/issues/${issue}/slack-messages/`;
  }
  async status(slug: string): Promise<SlackStatus> {
    return (await this.get(this.workspace(slug))).data;
  }
  async setupStatus(slug: string): Promise<SlackSetupState> {
    return (await this.get(this.workspace(slug) + "setup/")).data;
  }
  async saveSetup(slug: string, data: SlackSetupCredentials): Promise<SlackSetupState> {
    return (await this.put(this.workspace(slug) + "setup/", data)).data;
  }
  async connect(slug: string): Promise<{ url: string }> {
    return (await this.post(this.workspace(slug) + "connect/", {})).data;
  }
  async disconnect(slug: string, connection: string) {
    await this.delete(this.workspace(slug) + `connections/${connection}/`);
  }
  async channels(slug: string, connection: string): Promise<SlackChannel[]> {
    return (await this.get(this.workspace(slug) + `connections/${connection}/channels/`)).data;
  }
  async map(
    slug: string,
    data: { connection_id: string; project_id: string; channel_id: string }
  ): Promise<SlackMapping> {
    return (await this.post(this.workspace(slug) + "mappings/", data)).data;
  }
  async unmap(slug: string, mapping: string) {
    await this.delete(this.workspace(slug) + `mappings/${mapping}/`);
  }
  async sync(slug: string, mapping: string) {
    await this.post(this.workspace(slug) + `mappings/${mapping}/`, {});
  }
  async conversations(slug: string, project: string): Promise<SlackConversations> {
    return (await this.get(`/api/workspaces/${slug}/projects/${project}/slack-delivery/`)).data;
  }
  async issueMessages(slug: string, project: string, issue: string): Promise<SlackLinkedMessage[]> {
    return (await this.get(this.issue(slug, project, issue))).data;
  }
  async link(slug: string, project: string, issue: string, url: string): Promise<SlackLinkedMessage[]> {
    return (await this.post(this.issue(slug, project, issue), { url })).data;
  }
  async unlink(slug: string, project: string, issue: string, message: string) {
    await this.delete(this.issue(slug, project, issue) + `${message}/`);
  }
}
export const slackDeliveryService = new SlackDeliveryService();
