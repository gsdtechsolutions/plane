/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

/**
 * @description Fork customization: replaces the CE "Community" edition badge / upgrade entry
 * point with the fork's own project badge link. No upgrade modal is exposed.
 */
const prospectDevelopmentTeamLogo = "/plane-logos/pdt-logo.svg";

export function WorkspaceEditionBadge() {
  return (
    <a
      href="https://github.com/Prospect-Development-Team"
      target="_blank"
      rel="noopener noreferrer"
      className="mx-auto flex select-none items-center gap-2 rounded-full px-3 py-1 transition-colors hover:bg-layer-2"
    >
      <img
        src={prospectDevelopmentTeamLogo}
        alt="Prospect Development Team"
        className="h-6 w-6 rounded-sm object-cover"
      />
    </a>
  );
}
