/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors. SPDX-License-Identifier: AGPL-3.0-only
 */

import React, { useEffect, useState } from "react";
import useSWR from "swr";
import { Button } from "@makeplane/propel/components/button";
// services
import {
  AIConfigService,
  type TAIConfiguration,
  type TAIConfigurationSaveResponse,
  type TAIProvider,
} from "@/services/integrations/ai-config.service";

const aiConfigService = new AIConfigService();

const PROVIDER_OPTIONS: { value: TAIProvider; label: string; hint: string }[] = [
  { value: "openai", label: "OpenAI-compatible", hint: "OpenAI, LiteLLM, vLLM, DeepSeek, …" },
  { value: "gemini", label: "Gemini (OpenAI-compatible)", hint: "Google AI Studio endpoint" },
  { value: "anthropic", label: "Anthropic", hint: "Claude messages API" },
];

const FIELD_CLASS =
  "w-full rounded-md border border-subtle bg-surface-1 px-3 py-2 text-13 text-primary placeholder:text-tertiary outline-none focus:border-strong";
const LABEL_CLASS = "text-12 font-medium text-secondary";

type SaveState =
  | { kind: "idle" }
  | { kind: "saving" }
  | { kind: "ok"; model: string }
  | { kind: "saved-test-failed"; error: string }
  | { kind: "error"; error: string };

export function AIConfigurationSection({ workspaceSlug }: { workspaceSlug: string }) {
  // Ownership is enforced server-side (GET/POST 403 for everyone but the workspace
  // owner); the card simply renders when the fetch succeeds and hides otherwise.
  const { data, error, mutate } = useSWR<TAIConfiguration>(
    ["ai-configuration", workspaceSlug],
    () => aiConfigService.getAIConfig(workspaceSlug),
    { revalidateOnFocus: false }
  );

  const [provider, setProvider] = useState<TAIProvider>("openai");
  const [model, setModel] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [saveState, setSaveState] = useState<SaveState>({ kind: "idle" });
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    if (data && !loaded) {
      setProvider((data.llm_provider || "openai") as TAIProvider);
      setModel(data.llm_model || "");
      setBaseUrl(data.llm_base_url || "");
      setLoaded(true);
    }
  }, [data, loaded]);

  // Silently hidden for everyone but the owner (403 on the GET) and while loading.
  if (error || !data) return null;

  const configured = data.configured;

  const handleSave = async () => {
    setSaveState({ kind: "saving" });
    try {
      const response: TAIConfigurationSaveResponse = await aiConfigService.saveAIConfig(workspaceSlug, {
        llm_provider: provider,
        llm_model: model,
        llm_base_url: baseUrl,
        llm_api_key: apiKey || undefined,
        test: true,
      });
      setApiKey("");
      await mutate();
      if (response.test_ok) setSaveState({ kind: "ok", model: response.test_model || model });
      else setSaveState({ kind: "saved-test-failed", error: response.test_error || "The provider did not answer." });
    } catch (err) {
      setSaveState({ kind: "error", error: String((err as { detail?: string })?.detail ?? err) });
    }
  };

  return (
    <section className="border-b border-subtle pb-6">
      <div className="flex items-start justify-between gap-4 py-4">
        <div>
          <h3 className="text-15 font-semibold text-primary">AI configuration</h3>
          <p className="mt-1 text-13 text-secondary">
            Powers summaries, triage suggestions, workspace Q&amp;A and Slack answers. Only you (the workspace owner)
            can see and change this.
          </p>
        </div>
        <span
          className={`shrink-0 rounded-full px-2.5 py-1 text-11 font-medium ${
            configured ? "bg-accent-subtle text-accent-primary" : "bg-layer-1 text-tertiary"
          }`}
        >
          {configured ? "Configured" : "Not configured"}
        </span>
      </div>

      <div className="rounded-lg border border-subtle bg-layer-1 p-4">
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <div>
            <label className={LABEL_CLASS} htmlFor="ai-config-provider">
              Provider
            </label>
            <select
              id="ai-config-provider"
              className={`${FIELD_CLASS} mt-1.5`}
              value={provider}
              onChange={(e) => setProvider(e.target.value as TAIProvider)}
            >
              {PROVIDER_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
            <p className="mt-1 text-11 text-tertiary">
              {PROVIDER_OPTIONS.find((option) => option.value === provider)?.hint}
            </p>
          </div>
          <div>
            <label className={LABEL_CLASS} htmlFor="ai-config-model">
              Model
            </label>
            <input
              id="ai-config-model"
              className={`${FIELD_CLASS} mt-1.5`}
              value={model}
              onChange={(e) => setModel(e.target.value)}
              placeholder="e.g. gpt-5.1, claude-sonnet-4-5, glm-5.3-flash"
            />
          </div>
          <div>
            <label className={LABEL_CLASS} htmlFor="ai-config-base-url">
              Base URL <span className="font-normal text-tertiary">(optional)</span>
            </label>
            <input
              id="ai-config-base-url"
              className={`${FIELD_CLASS} mt-1.5`}
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              placeholder="https://llm.gsdut.dev/v1"
            />
          </div>
          <div>
            <label className={LABEL_CLASS} htmlFor="ai-config-api-key">
              API key
            </label>
            <input
              id="ai-config-api-key"
              type="password"
              autoComplete="off"
              className={`${FIELD_CLASS} mt-1.5`}
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder={data.llm_api_key_masked ? `${data.llm_api_key_masked} — leave blank to keep` : "sk-…"}
            />
          </div>
        </div>

        <div className="mt-4 flex items-center justify-between gap-3">
          <p className="min-w-0 flex-1 text-11 text-tertiary">
            The key is stored encrypted and never returned by the API. Saving runs a one-token live test call.
          </p>
          <Button
            variant="primary"
            label="Save and test"
            size="md"
            stretch="auto"
            loading={saveState.kind === "saving"}
            onClick={handleSave}
          />
        </div>

        {saveState.kind === "ok" && (
          <div className="mt-3 rounded-md bg-accent-subtle px-3 py-2 text-12 text-accent-primary">
            Saved — live test answered with {saveState.model}. AI features are now live across the workspace.
          </div>
        )}
        {saveState.kind === "saved-test-failed" && (
          <div className="mt-3 rounded-md bg-danger-subtle px-3 py-2 text-12 text-danger-primary">
            Saved, but the live test failed: {saveState.error}
          </div>
        )}
        {saveState.kind === "error" && (
          <div className="mt-3 rounded-md bg-danger-subtle px-3 py-2 text-12 text-danger-primary">
            Could not save: {saveState.error}
          </div>
        )}
      </div>
    </section>
  );
}
