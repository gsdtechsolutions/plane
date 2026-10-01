/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

/**
 * Fork feature: custom work-item property fields in the issue detail sidebar.
 * Renders the project's ACTIVE properties as typed fields and persists the
 * full values map via PUT on change/blur. Gated on at least one active
 * property existing.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { Check, ChevronDown } from "lucide-react";
import { observer } from "mobx-react";
// plane imports
import {
  CalendarOutline,
  CheckSquareOutline,
  DropdownOutline,
  HashOutline,
  TextOutline,
} from "@makeplane/propel/icons";
import { DateSelect } from "@plane/blocks/property-select";
import { Popover, PopoverContent, PopoverTrigger } from "@makeplane/propel/components/popover";
import { Checkbox } from "@makeplane/propel/components/checkbox";
import { setToast } from "@plane/blocks/toast";
import { useTranslation } from "@plane/i18n";
import { getDate } from "@plane/utils";
import type { ICustomProperty, TCustomPropertyValue } from "@/services/custom-properties/custom-property.service";
import { CustomPropertyService } from "@/services/custom-properties/custom-property.service";
// components
import { SidebarGroupHeader, SidebarPropertyListItem } from "@/components/common/layout/sidebar/property-list-item";
import { SidebarSectionCard } from "@/components/common/layout/sidebar/section-card";
// hooks
import { useUserProfile } from "@/hooks/store/user";

type Props = {
  workspaceSlug: string;
  projectId: string;
  issueId: string;
  disabled: boolean;
  /** Render as a Jira-style collapsible section card instead of a bare group of rows. */
  asCard?: boolean;
};

const PROPERTY_TYPE_ICONS = {
  text: TextOutline,
  number: HashOutline,
  date: CalendarOutline,
  select: DropdownOutline,
  checkbox: CheckSquareOutline,
} as const;

function OptionDot(props: { color: string }) {
  return (
    <span
      className="size-2.5 shrink-0 rounded-full border border-subtle-1"
      style={{ backgroundColor: props.color }}
    />
  );
}

function SelectedOptionLabels(props: { property: ICustomProperty; value: string[] }) {
  const { property, value } = props;
  const { t } = useTranslation();
  const options = (property.settings_json?.options ?? []).filter((option) => value.includes(option.id));
  if (options.length === 0) {
    return <span className="text-placeholder">{t("custom_properties.issue.none")}</span>;
  }
  return (
    <span className="flex min-w-0 flex-wrap items-center gap-1.5">
      {options.map((option) => (
        <span key={option.id} className="flex items-center gap-1 truncate text-body-xs-regular text-primary">
          <OptionDot color={option.color} />
          {option.name}
        </span>
      ))}
    </span>
  );
}

export const IssueCustomProperties = observer(function IssueCustomProperties(props: Props) {
  const { workspaceSlug, projectId, issueId, disabled, asCard = false } = props;
  const { t } = useTranslation();
  const { data: userProfile } = useUserProfile();

  const service = useMemo(() => new CustomPropertyService(), []);

  // data
  const [loading, setLoading] = useState(true);
  const [properties, setProperties] = useState<ICustomProperty[]>([]);
  const [values, setValues] = useState<Record<string, TCustomPropertyValue>>({});
  const [savingIds, setSavingIds] = useState<Set<string>>(new Set());

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    service
      .getIssueValues(workspaceSlug, projectId, issueId)
      .then((response) => {
        if (cancelled) return;
        setProperties(response.properties ?? []);
        setValues(response.property_values ?? {});
      })
      .catch(() => {
        // Silent: the sidebar must not break over custom props.
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [service, workspaceSlug, projectId, issueId]);

  const activeProperties = useMemo(() => properties.filter((property) => property.is_active), [properties]);

  const saveValues = useCallback(
    async (nextValues: Record<string, TCustomPropertyValue>, changedId: string) => {
      if (savingIds.has(changedId)) return;
      setSavingIds((prev) => new Set(prev).add(changedId));
      try {
        const response = await service.putIssueValues(workspaceSlug, projectId, issueId, nextValues);
        setProperties(response.properties ?? []);
        setValues(response.property_values ?? {});
      } catch (error) {
        setValues(values);
        const serverErrors = (error as { property_values?: Record<string, string> } | undefined)?.property_values;
        const firstMessage = serverErrors ? Object.values(serverErrors)[0] : undefined;
        setToast({
          type: "error",
          title: "Error!",
          message: firstMessage ?? t("custom_properties.toasts.save_error"),
        });
      } finally {
        setSavingIds((prev) => {
          const next = new Set(prev);
          next.delete(changedId);
          return next;
        });
      }
    },
    [service, workspaceSlug, projectId, issueId, values, savingIds, t]
  );

  const handleChange = (property: ICustomProperty, value: TCustomPropertyValue) => {
    const nextValues = { ...values, [property.id]: value };
    setValues(nextValues);
    void saveValues(nextValues, property.id);
  };

  const optionsOf = (property: ICustomProperty) => property.settings_json?.options ?? [];
  const selectValue = (property: ICustomProperty): string[] => {
    const value = values[property.id];
    if (Array.isArray(value)) return value;
    if (typeof value === "string" && value !== "") return [value];
    return [];
  };

  const renderField = (property: ICustomProperty) => {
    const value = values[property.id];
    const settings = property.settings_json ?? {};
    switch (property.type) {
      case "text":
        return (
          <input
            value={typeof value === "string" ? value : ""}
            disabled={disabled}
            placeholder={t("custom_properties.issue.text_placeholder")}
            className="h-7.5 w-full rounded-md border border-transparent bg-transparent px-2 text-body-xs-regular text-primary placeholder:text-placeholder focus:border-subtle focus:outline-none disabled:opacity-60"
            onChange={(e) => setValues((prev) => ({ ...prev, [property.id]: e.target.value }))}
            onBlur={(e) => handleChange(property, e.target.value === "" ? null : e.target.value)}
          />
        );
      case "number": {
        const stringValue = typeof value === "number" ? String(value) : "";
        return (
          <input
            type="number"
            step="any"
            value={stringValue}
            disabled={disabled}
            placeholder={settings.unit ? `0 ${settings.unit}` : t("custom_properties.issue.number_placeholder")}
            className="h-7.5 w-full rounded-md border border-transparent bg-transparent px-2 text-body-xs-regular text-primary placeholder:text-placeholder focus:border-subtle focus:outline-none disabled:opacity-60"
            onChange={(e) => setValues((prev) => ({ ...prev, [property.id]: e.target.value }))}
            onBlur={(e) => {
              const raw = e.target.value.trim();
              if (raw === "") {
                handleChange(property, null);
                return;
              }
              const parsed = Number(raw);
              if (Number.isNaN(parsed)) {
                setValues(values);
                return;
              }
              handleChange(property, parsed);
            }}
          />
        );
      }
      case "date":
        return (
          <DateSelect
            placeholder={t("custom_properties.issue.none")}
            value={getDate(typeof value === "string" ? value : undefined) ?? null}
            onChange={(val) =>
              handleChange(property, val ? val.toISOString().slice(0, 10) : null)
            }
            disabled={disabled}
            clearable
            weekStartsOn={userProfile?.start_of_the_week}
            variant="select-ghost-md"
            showTooltip
            tooltipHeading={property.name}
          />
        );
      case "select": {
        const options = optionsOf(property);
        const selected = selectValue(property);
        if (options.length === 0) return null;
        return (
          <Popover>
            <PopoverTrigger
              disabled={disabled}
              render={
                <button
                  type="button"
                  className="flex h-7.5 w-full items-center justify-between gap-1 rounded-md px-2 text-left text-body-xs-regular hover:bg-layer-1 disabled:opacity-60"
                >
                  <SelectedOptionLabels property={property} value={selected} />
                  <ChevronDown className="size-3 shrink-0 text-tertiary" />
                </button>
              }
            />
            <PopoverContent variant="rich" side="bottom" align="end">
              <div className="max-h-64 w-56 overflow-y-auto p-1">
                {options.map((option) => {
                  const isSelected = selected.includes(option.id);
                  return (
                    <button
                      key={option.id}
                      type="button"
                      className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-body-xs-regular text-primary transition-colors hover:bg-layer-2"
                      onClick={() => {
                        const next = isSelected
                          ? selected.filter((id) => id !== option.id)
                          : [...selected, option.id];
                        handleChange(property, next);
                      }}
                    >
                      <OptionDot color={option.color} />
                      <span className="min-w-0 grow truncate">{option.name}</span>
                      {isSelected && <Check className="size-3 shrink-0 text-primary" />}
                    </button>
                  );
                })}
              </div>
            </PopoverContent>
          </Popover>
        );
      }
      case "checkbox":
        return (
          <div className="h-7.5 flex items-center px-2">
            <Checkbox
              checked={value === true}
              disabled={disabled}
              onCheckedChange={(checked) => handleChange(property, checked === true)}
            />
          </div>
        );
      default:
        return null;
    }
  };

  if (loading) return null;
  if (activeProperties.length === 0) return null;

  const rows = activeProperties.map((property) => {
    const Icon = PROPERTY_TYPE_ICONS[property.type] ?? TextOutline;
    const settings = property.settings_json ?? {};
    return (
      <SidebarPropertyListItem
        key={property.id}
        icon={Icon}
        label={`${property.name}${settings.required ? " *" : ""}`}
        variant={asCard ? "stacked" : "inline"}
      >
        {renderField(property)}
      </SidebarPropertyListItem>
    );
  });

  if (asCard) return <SidebarSectionCard label={t("common.custom_fields")}>{rows}</SidebarSectionCard>;

  return (
    <>
      <SidebarGroupHeader label={t("common.custom_fields")} className="pt-3" />
      {rows}
    </>
  );
});
