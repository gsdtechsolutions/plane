/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useState } from "react";
// local imports
import { buildInstallSnippet, isValidPublicUrl } from "./embed-urls";

/**
 * Public feed contract served by the releases backend (published-only,
 * newest-first). The setup component only needs to display and validate it —
 * it never publishes.
 */
export type TReleaseFeedPayload = {
  project_name: string;
  releases: Array<{
    id: string;
    name: string;
    version: string;
    notes: string;
    published_at: string;
    app_version: string | null;
  }>;
};

export type ReleaseEmbedSetupProps = {
  /** Absolute http(s) URL of the project's public releases feed. */
  feedUrl: string;
  /** Absolute http(s) URL where gsd-whats-new.js is hosted. */
  scriptUrl: string;
};

/**
 * Setup panel for embedding the What's New widget into a connected app.
 *
 * The widget is read-only: it fetches the public releases feed and renders
 * published notes. Publishing stays manual in the board's Releases area, and
 * app-version reporting requires the connected-app integration to exist —
 * neither is claimed or simulated here.
 */
export function ReleaseEmbedSetup(props: ReleaseEmbedSetupProps) {
  const { feedUrl, scriptUrl } = props;
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!copied) return;
    const timer = window.setTimeout(() => setCopied(false), 2000);
    return () => window.clearTimeout(timer);
  }, [copied]);

  const valid = isValidPublicUrl(feedUrl) && isValidPublicUrl(scriptUrl);
  const snippet = buildInstallSnippet(feedUrl, scriptUrl);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(snippet);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  };

  if (!valid) {
    return (
      <p className="text-sm text-danger-primary" role="alert">
        Feed and script URLs must be absolute http(s) addresses before an embed snippet can be generated.
      </p>
    );
  }

  return (
    <div className="space-y-3">
      <p className="text-sm text-secondary">
        Paste this snippet before the closing <code>&lt;/body&gt;</code> tag of the connected app. The launcher renders
        bottom-right; visitors open it themselves.
      </p>
      <pre className="overflow-x-auto rounded-md border border-subtle bg-surface-2 p-3 text-xs">
        <code>{snippet}</code>
      </pre>
      <button
        type="button"
        onClick={handleCopy}
        className="rounded-sm border border-subtle px-3 py-1.5 text-xs font-medium hover:bg-surface-2"
      >
        {copied ? "Copied" : "Copy snippet"}
      </button>
      <ol className="list-decimal space-y-1.5 pl-5 text-sm text-secondary">
        <li>
          Publish release notes in the board&apos;s Releases area — the feed exposes <em>published</em> entries only,
          newest first.
        </li>
        <li>
          Point <code>data-feed</code> at this project&apos;s public feed (
          <code>/api/public/anchor/&lt;anchor&gt;/releases/</code>) — the URL above is prefilled with the current
          project&apos;s anchor.
        </li>
        <li>
          Optionally add <code>data-app-version</code> with the connected app&apos;s reported version — the widget marks
          the matching release as &quot;your version&quot;.
        </li>
      </ol>
      <p className="text-xs text-tertiary">
        The widget is read-only and credentialess: it performs one GET against the feed per open and never sends cookies,
        publishes, or mutates anything.
      </p>
    </div>
  );
}
