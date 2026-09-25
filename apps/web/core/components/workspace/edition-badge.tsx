/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

/**
 * @description Fork customization: replaces the CE "Community" edition badge / upgrade entry
 * point with the fork's own project badge link. No upgrade modal is exposed.
 */
const gsdTechSolutionsLogo = "/plane-logos/gsd-logo.png";

export function WorkspaceEditionBadge() {
  return (
    <a
      href="https://gsdtechsolutions.com/"
      target="_blank"
      rel="noopener noreferrer"
      className="mx-auto flex select-none items-center rounded-lg bg-[#151212] px-3 py-2 transition-opacity hover:opacity-80 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent-primary"
    >
      <img src={gsdTechSolutionsLogo} alt="GSD Tech Solutions" className="h-7 w-auto object-contain" />
    </a>
  );
}
