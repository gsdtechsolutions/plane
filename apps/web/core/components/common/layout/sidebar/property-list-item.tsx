/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import type { ReactNode } from "react";
import { cn } from "@plane/utils";

type TSidebarPropertyListItemProps = {
  icon: React.FC<{ className?: string }>;
  label: string;
  children: ReactNode;
  appendElement?: ReactNode;
  childrenClassName?: string;
};

export function SidebarPropertyListItem(props: TSidebarPropertyListItemProps) {
  const { icon: Icon, label, children, appendElement, childrenClassName } = props;

  return (
    <div className="flex items-start gap-2">
      <div className="flex h-7.5 w-30 shrink-0 items-center gap-1.5 text-body-xs-regular text-tertiary">
        <Icon className="size-4 shrink-0" />
        <span>{label}</span>
        {appendElement}
      </div>
      <div className={cn("flex grow flex-wrap items-center gap-1", childrenClassName)}>{children}</div>
    </div>
  );
}

type TSidebarGroupHeaderProps = {
  label: string;
  className?: string;
};

/** Tiny muted section divider used to group related rows inside the properties panel. */
export function SidebarGroupHeader(props: TSidebarGroupHeaderProps) {
  const { label, className } = props;

  return (
    <div className={cn("flex items-center gap-2 pb-0.5", className)}>
      <span className="text-11 font-medium uppercase tracking-wide text-tertiary">{label}</span>
    </div>
  );
}
