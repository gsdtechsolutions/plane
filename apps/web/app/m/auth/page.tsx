/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { redirect } from "react-router";
import type { Route } from "./+types/page";
import { isSafeRedirectPath } from "./safe-redirect-path";

/**
 * Mobile app auth bridge route.
 *
 * The official Plane mobile app opens /m/auth (plus the /m/sign-in and /m/sign-up
 * aliases) on the instance to kick off web sign-in. CE has no such route, so it
 * 404'd on self-hosted installs (native mobile support itself remains Cloud/
 * Commercial-only); this route just makes the web sign-in reachable.
 *
 * Redirect safety:
 * - The redirect target is the CONSTANT root path "/" — no caller-controlled URL
 *   is ever used, so this route itself cannot be abused as an open redirect.
 * - Query params that downstream auth handling treats as post-login redirect
 *   targets (AuthenticationWrapper reads `next_path`) are only forwarded when
 *   they pass the root-relative whitelist in ./safe-redirect-path; unsafe or
 *   empty values are dropped entirely so the app's weaker URL validation is
 *   never relied upon from this bridge. All other params are carried over
 *   verbatim.
 *
 * Logged-out users then land on the sign-in screen at "/", signed-in users on
 * their workspace home. Works with or without a trailing slash: react-router
 * matches both, and the app-shell middleware canonicalizes to the trailing-slash
 * form with a 308.
 */
const REDIRECT_TARGET_PARAMS = new Set([
  "next_path",
  "next",
  "redirect",
  "redirect_to",
  "redirectTo",
  "returnTo",
  "return_to",
]);

export const clientLoader = ({ request }: Route.ClientLoaderArgs) => {
  const incoming = new URL(request.url).searchParams;
  const forwarded = new URLSearchParams();
  incoming.forEach((value, key) => {
    if (REDIRECT_TARGET_PARAMS.has(key) && !isSafeRedirectPath(value)) return;
    forwarded.append(key, value);
  });
  const query = forwarded.toString();
  throw redirect(query ? `/?${query}` : "/");
};

export default function MobileAuthBridgePage() {
  return null;
}
