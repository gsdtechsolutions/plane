/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

/**
 * Whitelist for post-auth redirect paths (e.g. the `next_path` query param the
 * sign-in flow consumes via AuthenticationWrapper).
 *
 * Only single-root-relative paths are accepted:
 * - the value must start with exactly one "/" — NOT "//" or "/\", which browsers
 *   treat as protocol-relative references to an external host;
 * - no backslash anywhere (browsers fold "\" into "/" so "/\host" is external);
 * - no embedded "://" scheme (e.g. "/https://evil");
 * - no control characters (raw \n, \r, \t, \0 ... are rejected after decoding).
 *
 * Anything else must be DROPPED by callers — never forwarded to downstream
 * redirect handling, whose own URL validation is deliberately not relied upon.
 */
export const isSafeRedirectPath = (value: string | null | undefined): value is string => {
  if (!value) return false;
  if (!value.startsWith("/")) return false;
  if (value.startsWith("//") || value.startsWith("/\\")) return false;
  if (value.includes("\\") || value.includes("://")) return false;
  for (let index = 0; index < value.length; index++) {
    const charCode = value.charCodeAt(index);
    if (charCode <= 0x1f || charCode === 0x7f) return false;
  }
  return true;
};
