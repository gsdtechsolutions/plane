/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useState } from "react";
import type { ReactNode } from "react";
import { ChevronDownOutline, ChevronRightOutline } from "@makeplane/propel/icons";
import { cn } from "@plane/utils";

type TSidebarSectionCardProps = {
  label: string;
  children: ReactNode;
  /** Collapse the section by default (rarely used). */
  defaultOpen?: boolean;
  className?: string;
  /** Extra node rendered at the right edge of the header row (e.g. a count). */
  appendElement?: ReactNode;
};

/**
 * Jira-style collapsible panel: a rounded, bordered, elevated card with a chevroned
 * header, used to group property rows (Details / Planning / Custom fields …).
 */
export function SidebarSectionCard(props: TSidebarSectionCardProps) {
  const { label, children, defaultOpen = true, className, appendElement } = props;
  const [open, setOpen] = useState(defaultOpen);

  return (
    <div className={cn("rounded-xl border border-subtle bg-layer-1", className)}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-1.5 px-4 py-3 text-body-xs-medium text-secondary"
        aria-expanded={open}
      >
        {open ? <ChevronDownOutline className="size-3.5 text-tertiary" /> : <ChevronRightOutline className="size-3.5 text-tertiary" />}
        <span>{label}</span>
        {appendElement && <span className="ml-auto">{appendElement}</span>}
      </button>
      {open && <div className="px-4 pb-3">{children}</div>}
    </div>
  );
}
