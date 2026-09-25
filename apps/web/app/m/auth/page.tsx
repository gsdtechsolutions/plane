/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { redirect } from "react-router";
import type { Route } from "./+types/page";

/**
 * Mobile app auth bridge route.
 *
 * The official Plane mobile app opens /m/auth (plus the /m/sign-in and /m/sign-up
 * aliases) on the instance to kick off web sign-in. CE has no such route, so it
 * 404'd on self-hosted installs (native mobile support itself remains Cloud/
 * Commercial-only); this route just makes the web sign-in reachable.
 *
 * The redirect target is the constant root path "/" — no caller-controlled URL is
 * ever used, so this cannot be abused as an open redirect. The request's own
 * search params are carried over verbatim so deep-link targets (e.g. next
 * params) survive. Logged-out users then land on the sign-in screen at "/", and
 * signed-in users land on their workspace home. Works with or without a trailing
 * slash: react-router matches both, and the app-shell middleware canonicalizes
 * to the trailing-slash form with a 308.
 */
export const clientLoader = ({ request }: Route.ClientLoaderArgs) => {
  const searchParams = new URL(request.url).searchParams;
  const query = searchParams.toString();
  throw redirect(query ? `/?${query}` : "/");
};

export default function MobileAuthBridgePage() {
  return null;
}
