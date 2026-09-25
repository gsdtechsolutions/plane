/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

/**
 * True when the value is an absolute http(s) URL suitable for a public feed
 * or script source. Validated through the URL constructor — aligned with
 * gsd-whats-new.js — so relative paths, protocol-relative hosts, non-http
 * schemes, and URLs embedding credentials are all rejected.
 */
export const isValidPublicUrl = (value: string): boolean => {
  if (typeof value !== "string") return false;
  const trimmed = value.trim();
  if (!trimmed || trimmed.length > 2048) return false;
  try {
    const parsed = new URL(trimmed);
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return false;
    if (!parsed.hostname) return false;
    if (parsed.username || parsed.password) return false;
    return true;
  } catch {
    return false;
  }
};

/**
 * Builds the copyable install snippet. Both inputs are inserted into an HTML
 * attribute context, so quotes are escaped to keep the snippet safe to paste
 * even when a URL contains a quote character.
 */
export const buildInstallSnippet = (feedUrl: string, scriptUrl: string): string => {
  const escapeAttr = (value: string) => value.replace(/&/g, "&amp;").replace(/"/g, "&quot;");
  return `<script src="${escapeAttr(scriptUrl)}" data-feed="${escapeAttr(feedUrl)}" defer></script>`;
};
