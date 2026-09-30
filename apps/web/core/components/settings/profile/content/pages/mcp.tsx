/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useMemo, useState } from "react";
import { observer } from "mobx-react";
// plane imports
import { useTranslation } from "@plane/i18n";
import { setToast } from "@plane/blocks/toast";
import { Button } from "@makeplane/propel/components/button";
import { CopyOutline } from "@makeplane/propel/icons";
import { copyTextToClipboard } from "@plane/utils";
// components
import { ProfileSettingsHeading } from "@/components/settings/profile/heading";
// hooks
import { useCommandPalette } from "@/hooks/store/use-command-palette";

const TOKEN_PLACEHOLDER = "your-personal-access-token";

function useMcpOrigin(): string {
  // rendered inside the client-side settings modal; guard keeps SSR builds happy
  return typeof window === "undefined" ? "" : window.location.origin;
}

function CopyConfigButton({ value, copiedLabel, copyLabel }: { value: string; copiedLabel: string; copyLabel: string }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    await copyTextToClipboard(value);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <button
      type="button"
      onClick={handleCopy}
      className="flex flex-shrink-0 items-center gap-1.5 rounded-sm border border-subtle px-2.5 py-1.5 text-xs font-medium hover:bg-surface-2"
    >
      <CopyOutline className="h-3.5 w-3.5" />
      {copied ? copiedLabel : copyLabel}
    </button>
  );
}

function ConfigCard({ name, note, config }: { name: string; note?: string; config: string }) {
  const { t } = useTranslation();

  return (
    <div className="rounded-md border border-subtle bg-surface-1 p-3">
      <div className="mb-2 flex items-center justify-between gap-2">
        <div className="min-w-0">
          <p className="text-caption-md-medium">{name}</p>
          {note && <p className="text-caption-md-regular text-tertiary">{note}</p>}
        </div>
        <CopyConfigButton value={config} copyLabel={t("account_settings.mcp.copy")} copiedLabel={t("account_settings.mcp.copied")} />
      </div>
      <pre className="overflow-x-auto rounded-md border border-subtle bg-surface-2 p-3 text-xs">
        <code>{config}</code>
      </pre>
    </div>
  );
}

export const MCPProfileSettings = observer(function MCPProfileSettings() {
  // store hooks
  const { toggleProfileSettingsModal } = useCommandPalette();
  // translation
  const { t } = useTranslation();
  // derived values
  const origin = useMcpOrigin();
  const serverUrl = `${origin}/mcp`;

  const configs = useMemo(
    () => [
      {
        name: "Codex CLI",
        config: `codex mcp add plane --url ${serverUrl} --bearer-token-env-var PLANE_API_KEY\nexport PLANE_API_KEY=${TOKEN_PLACEHOLDER}`,
      },
      {
        name: "Claude Code",
        config: `claude mcp add --transport http plane ${serverUrl} \\\n  --header "Authorization: Bearer ${TOKEN_PLACEHOLDER}"`,
      },
      {
        name: "Cursor",
        note: "~/.cursor/mcp.json",
        config: JSON.stringify(
          {
            mcpServers: {
              plane: {
                url: serverUrl,
                headers: { Authorization: `Bearer ${TOKEN_PLACEHOLDER}` },
              },
            },
          },
          null,
          2
        ),
      },
      {
        name: "ZCode",
        note: "mcpServers config",
        config: JSON.stringify(
          {
            mcpServers: {
              plane: {
                type: "http",
                url: serverUrl,
                headers: { Authorization: `Bearer ${TOKEN_PLACEHOLDER}` },
              },
            },
          },
          null,
          2
        ),
      },
      {
        name: "Test with the MCP Inspector",
        config: `npx @modelcontextprotocol/inspector --cli ${serverUrl} --transport http \\\n  --header "Authorization: Bearer ${TOKEN_PLACEHOLDER}" \\\n  --method tools/list`,
      },
    ],
    [serverUrl]
  );

  return (
    <div className="size-full">
      <ProfileSettingsHeading
        title={t("account_settings.mcp.title")}
        description={t("account_settings.mcp.description")}
        control={
          <Button
            variant="primary"
            size="md"
            stretch="auto"
            label={t("account_settings.mcp.open_tokens")}
            onClick={() => toggleProfileSettingsModal({ activeTab: "api-tokens", isOpen: true })}
          />
        }
      />

      <div className="mt-7 space-y-6">
        <section>
          <p className="mb-2 text-caption-md-medium text-secondary">{t("account_settings.mcp.server_url")}</p>
          <div className="flex items-center gap-2">
            <code className="min-w-0 flex-1 truncate rounded-md border border-subtle bg-surface-2 px-3 py-2 text-xs">
              {serverUrl}
            </code>
            <CopyConfigButton value={serverUrl} copyLabel={t("account_settings.mcp.copy")} copiedLabel={t("account_settings.mcp.copied")} />
          </div>
        </section>

        <section>
          <p className="mb-2 text-caption-md-medium text-secondary">{t("account_settings.mcp.step_token")}</p>
          <p className="text-sm text-secondary">{t("account_settings.mcp.step_token_hint")}</p>
        </section>

        <section>
          <p className="mb-2 text-caption-md-medium text-secondary">{t("account_settings.mcp.step_client")}</p>
          <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
            {configs.map((config) => (
              <ConfigCard key={config.name} name={config.name} note={config.note} config={config.config} />
            ))}
          </div>
        </section>

        <p className="text-xs text-tertiary">{t("account_settings.mcp.warning")}</p>
      </div>
    </div>
  );
});
