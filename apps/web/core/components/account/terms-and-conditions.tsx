/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { EAuthModes } from "@plane/constants";

interface TermsAndConditionsProps {
  authType?: EAuthModes;
}

/**
 * @description Fork customization: Plane legal links (Terms/Privacy) are intentionally not
 * shown on the auth screens. The component is kept so upstream auth layouts keep resolving.
 */
export function TermsAndConditions(_props: TermsAndConditionsProps) {
  return null;
}
