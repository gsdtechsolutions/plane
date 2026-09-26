/** Copyright (c) 2023-present Plane Software, Inc. and contributors. SPDX-License-Identifier: AGPL-3.0-only */
import { useState } from "react";
import { ChevronDown } from "lucide-react";

type IntegrationDisclosureProps = {
  /** Stable per-section key; open/closed state persists under this id. */
  id: string;
  icon?: React.ReactNode;
  title: string;
  description?: React.ReactNode;
  /** Small status badge rendered next to the title (e.g. Connected). */
  status?: React.ReactNode;
  /** Right-aligned actions rendered outside the toggle (e.g. Connect buttons). */
  actions?: React.ReactNode;
  defaultOpen?: boolean;
  children: React.ReactNode;
};

function storedOpen(id: string, defaultOpen: boolean): boolean {
  try {
    const value = window.localStorage.getItem(`plane.integrations.disclosure.${id}`);
    return value === null ? defaultOpen : value === "open";
  } catch {
    return defaultOpen;
  }
}

/**
 * Collapsible section wrapper for the workspace Integrations page: every
 * integration renders inside one of these so long pages can be folded down
 * to a single header row per integration. The header (icon, title, status,
 * actions) lives here; sections render body-only content as children.
 */
export function IntegrationDisclosure({
  id,
  icon,
  title,
  description,
  status,
  actions,
  defaultOpen = true,
  children,
}: IntegrationDisclosureProps) {
  const [open, setOpen] = useState(() => storedOpen(id, defaultOpen));
  const toggle = () =>
    setOpen((value) => {
      const next = !value;
      try {
        window.localStorage.setItem(`plane.integrations.disclosure.${id}`, next ? "open" : "closed");
      } catch {
        // Persistence is best-effort; the toggle still works without it.
      }
      return next;
    });
  return (
    <section aria-labelledby={`integration-disclosure-${id}-heading`} className="border-b border-subtle py-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <button
          type="button"
          onClick={toggle}
          aria-expanded={open}
          aria-controls={`integration-disclosure-${id}-panel`}
          className="group flex min-w-0 flex-1 items-start gap-3 text-left"
        >
          {icon && <span className="mt-0.5 shrink-0">{icon}</span>}
          <span className="min-w-0">
            <span className="flex flex-wrap items-center gap-2">
              <h4 id={`integration-disclosure-${id}-heading`} className="text-16 font-semibold">
                {title}
              </h4>
              {status}
              <ChevronDown
                className={`size-4 text-secondary transition-transform ${open ? "" : "-rotate-90"}`}
                aria-hidden
              />
            </span>
            {description && <span className="mt-1 block max-w-xl text-13 text-secondary">{description}</span>}
          </span>
        </button>
        {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
      </div>
      {open && (
        <div id={`integration-disclosure-${id}-panel`} className="mt-5 space-y-5">
          {children}
        </div>
      )}
    </section>
  );
}
