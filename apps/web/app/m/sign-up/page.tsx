/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// Alias of the mobile-app auth bridge: /m/sign-up redirects to "/" exactly like
// /m/auth. Re-exported so both share the same page component/redirect logic.
export { clientLoader, default } from "../auth/page";
