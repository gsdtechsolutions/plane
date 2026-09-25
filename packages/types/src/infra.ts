/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// Fork feature: board ↔ infrastructure links (Coolify / Grafana).

export type TInfraService = "coolify" | "grafana";

export type TInfraLinkKind = "coolify_app" | "grafana_dashboard";

export interface IInfraConnection {
  id: string;
  name: string;
  service: TInfraService;
  base_url: string;
  has_token: boolean;
  created_at: string;
  updated_at: string;
}

export interface IInfraLink {
  id: string;
  project: string;
  connection: string;
  kind: TInfraLinkKind;
  external_id: string;
  display_name: string;
  external_url: string;
  meta: Record<string, unknown>;
  connection_info?: {
    id: string;
    name: string;
    service: TInfraService;
  };
  created_at: string;
  updated_at: string;
}

export interface IInfraDeployment {
  id: string;
  status: string | null;
  commit_sha: string | null;
  message: string | null;
  created_at: string | null;
  finished_at: string | null;
}

export interface IInfraCoolifyStatus {
  status: string | null;
  health: {
    enabled: boolean;
    path: string | null;
    port: number | null;
    check_period: number | null;
  };
  version: {
    label: string | null;
    image_tag: string | null;
    commit_sha: string | null;
    message: string | null;
    deployed_at: string | null;
  };
  app_url: string | null;
  deep_link: string | null;
  deployments: IInfraDeployment[];
}

export interface IInfraGrafanaStatus {
  kind: "grafana_dashboard";
  title: string;
  url: string;
}

export type TInfraLinkStatus = IInfraCoolifyStatus | IInfraGrafanaStatus | null;

export interface IInfraLinkStatusEntry {
  link: IInfraLink;
  status: TInfraLinkStatus;
  error: string | null;
}

export interface IInfraConnectionVerifyResult {
  ok: boolean;
  version?: string | null;
  service?: TInfraService;
  error?: string;
}

export interface IInfraConnectionResources {
  resource_type: "applications" | "dashboards";
  resources: {
    id: string;
    name: string;
    title?: string;
    fqdn?: string;
    description?: string;
    status?: string | null;
    url?: string;
    folder_title?: string;
    tags?: string[];
  }[];
  error?: string;
}

export type EInfraResource = IInfraConnectionResources["resources"][number];
