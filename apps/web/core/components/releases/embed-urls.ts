/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

const HTTP_URL_PATTERN = /^https?:\/\/[^\s]+$/i;

/**
 * True when the value is an absolute http(s) URL suitable for a public feed
 * or script source. Mirrors the widget's own validation so what the snippet
 * embeds is exactly what the widget accepts.
 */
export const isValidPublicUrl = (value: string): boolean =>
  typeof value === "string" && value.trim().length > 0 && value.length <= 2048 && HTTP_URL_PATTERN.test(value.trim());

/**
 * Builds the copyable install snippet. Both inputs are inserted into an HTML
 * attribute context, so quotes are escaped to keep the snippet safe to paste
 * even when a URL contains a quote character.
 */
export const buildInstallSnippet = (feedUrl: string, scriptUrl: string): string => {
  const escapeAttr = (value: string) => value.replace(/&/g, "&amp;").replace(/"/g, "&quot;");
  return `<script src="${escapeAttr(scriptUrl)}" data-feed="${escapeAttr(feedUrl)}" defer></script>`;
};
