/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors. SPDX-License-Identifier: AGPL-3.0-only
 */

import { API_BASE_URL } from "@plane/constants";
import { APIService } from "@/services/api.service";

export type TAIProvider = "openai" | "gemini" | "anthropic";

export interface TAIConfiguration {
  llm_provider: TAIProvider | "";
  llm_model: string;
  llm_base_url: string;
  llm_api_key_masked: string;
  configured: boolean;
}

export interface TAIConfigurationSavePayload {
  llm_provider: TAIProvider;
  llm_model: string;
  llm_base_url?: string;
  llm_api_key?: string;
  test?: boolean;
}

export interface TAIConfigurationSaveResponse {
  saved: boolean;
  tested?: boolean;
  test_ok?: boolean;
  test_model?: string;
  test_error?: string;
}

export class AIConfigService extends APIService {
  constructor() {
    super(API_BASE_URL);
  }

  async getAIConfig(workspaceSlug: string): Promise<TAIConfiguration> {
    return this.get(`/api/workspaces/${workspaceSlug}/ai-configuration/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data ?? error;
      });
  }

  async saveAIConfig(
    workspaceSlug: string,
    payload: TAIConfigurationSavePayload
  ): Promise<TAIConfigurationSaveResponse> {
    return this.post(`/api/workspaces/${workspaceSlug}/ai-configuration/`, payload)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data ?? error;
      });
  }
}
