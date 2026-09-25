/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

/**
 * Fork feature: custom work-item property manager on the project settings page.
 * Local state only — no mobx store needed (mirrors board-rules.tsx).
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { ChevronDown, ChevronUp, Pencil, Plus, SlidersHorizontal, Trash2, X } from "lucide-react";
// plane imports
import { EUserPermissions, EUserPermissionsLevel, getRandomLabelColor, LABEL_COLOR_OPTIONS } from "@plane/constants";
import { Select } from "@plane/blocks/select";
import { ConfirmDialog } from "@plane/blocks/dialog";
import { ColorSwatchPicker } from "@plane/blocks/common";
import { setToast } from "@plane/blocks/toast";
import { useTranslation } from "@plane/i18n";
import { Checkbox } from "@makeplane/propel/components/checkbox";
import { Input } from "@makeplane/propel/components/input";
import { Popover, PopoverContent, PopoverTrigger } from "@makeplane/propel/components/popover";
import { Switch } from "@makeplane/propel/components/switch";
// services
import {
  CustomPropertyService,
  type ICustomProperty,
  type ICustomPropertyOption,
  type TCustomPropertyType,
} from "@/services/custom-properties/custom-property.service";
// hooks
import { useUserPermissions } from "@/hooks/store/user";

const PROPERTY_TYPES: TCustomPropertyType[] = ["text", "number", "date", "select", "checkbox"];

const EMPTY_OPTION: { value: string; label: string } = { value: "", label: "" };

type Props = {
  workspaceSlug: string;
  projectId: string;
};

type PropertyDraft = {
  name: string;
  type: TCustomPropertyType;
  description: string;
  required: boolean;
  unit: string;
  options: ICustomPropertyOption[];
};

const EMPTY_DRAFT: PropertyDraft = {
  name: "",
  type: "text",
  description: "",
  required: false,
  unit: "",
  options: [],
};

function draftFromProperty(property: ICustomProperty): PropertyDraft {
  const settings = property.settings_json ?? {};
  return {
    name: property.name,
    type: property.type,
    description: settings.description ?? "",
    required: settings.required ?? false,
    unit: settings.unit ?? "",
    options: (settings.options ?? []).map((option) => ({ ...option })),
  };
}

function TypeSelect(props: {
  value: TCustomPropertyType;
  onChange: (value: TCustomPropertyType) => void;
  disabled: boolean;
}) {
  const { t } = useTranslation();
  const { value, onChange, disabled } = props;
  const options = PROPERTY_TYPES.map((type) => ({
    value: type,
    label: t(`custom_properties.types.${type}`),
  }));
  return (
    <Select<{ value: string; label: string }>
      getValues={() => options}
      value={options.find((option) => option.value === value) ?? EMPTY_OPTION}
      onChange={(selectedValue) => {
        const selected = options.find((option) => option.value === selectedValue);
        if (selected) onChange(selected.value as TCustomPropertyType);
      }}
      getOptionValue={(option) => option.value}
      getOptionLabel={(option) => option.label}
      disabled={disabled}
      showSearch={false}
      pinSelected={false}
      contentSizing="anchor"
    >
      <Select.Trigger<{ value: string; label: string }> variant="select-md" className="w-full max-w-56">
        <span className="min-w-0 grow truncate text-left">
          {options.find((option) => option.value === value)?.label ?? t("custom_properties.property_type")}
        </span>
      </Select.Trigger>
    </Select>
  );
}

export function CustomPropertiesManager(props: Props) {
  const { workspaceSlug, projectId } = props;
  const { t } = useTranslation();
  const { allowPermissions } = useUserPermissions();
  const isAdmin = allowPermissions([EUserPermissions.ADMIN], EUserPermissionsLevel.PROJECT);

  const service = useMemo(() => new CustomPropertyService(), []);

  // data
  const [loading, setLoading] = useState(true);
  const [properties, setProperties] = useState<ICustomProperty[]>([]);
  const [pendingIds, setPendingIds] = useState<Set<string>>(new Set());

  // form state
  const [formOpen, setFormOpen] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState<PropertyDraft>(EMPTY_DRAFT);
  const [saving, setSaving] = useState(false);

  // delete confirmation
  const [deleteTarget, setDeleteTarget] = useState<ICustomProperty | null>(null);
  const [deleting, setDeleting] = useState(false);

  const notify = useCallback(
    (messageKey: string, type: "success" | "error" = "success") =>
      setToast({
        type,
        title: type === "success" ? "Success!" : "Error!",
        message: t(messageKey),
      }),
    [t]
  );

  const fetchProperties = useCallback(async () => {
    if (!workspaceSlug || !projectId) return;
    setLoading(true);
    try {
      const data = await service.listProperties(workspaceSlug, projectId);
      setProperties(data);
    } catch {
      notify("custom_properties.toasts.load_error", "error");
    } finally {
      setLoading(false);
    }
  }, [service, workspaceSlug, projectId, notify]);

  useEffect(() => {
    void fetchProperties();
  }, [fetchProperties]);

  const resetForm = () => {
    setDraft(EMPTY_DRAFT);
    setEditingId(null);
  };

  const openCreate = () => {
    resetForm();
    setFormOpen(true);
  };

  const openEdit = (property: ICustomProperty) => {
    setDraft(draftFromProperty(property));
    setEditingId(property.id);
    setFormOpen(true);
  };

  const markPending = (id: string, pending: boolean) =>
    setPendingIds((prev) => {
      const next = new Set(prev);
      if (pending) next.add(id);
      else next.delete(id);
      return next;
    });

  const handleSave = async () => {
    if (!workspaceSlug || !projectId) return;
    const trimmedName = draft.name.trim();
    if (!trimmedName) {
      notify("custom_properties.toasts.validation", "error");
      return;
    }
    if (draft.type === "select" && draft.options.length === 0) {
      notify("custom_properties.toasts.validation", "error");
      return;
    }
    const settings_json: Record<string, unknown> = {
      required: draft.required,
      description: draft.description,
    };
    if (draft.type === "number") settings_json.unit = draft.unit;
    if (draft.type === "select") settings_json.options = draft.options;
    const payload = {
      name: trimmedName,
      type: draft.type,
      settings_json,
    };
    setSaving(true);
    try {
      if (editingId) {
        const updated = await service.updateProperty(workspaceSlug, projectId, editingId, payload);
        setProperties((prev) => prev.map((item) => (item.id === updated.id ? updated : item)));
        notify("custom_properties.toasts.updated");
      } else {
        const created = await service.createProperty(workspaceSlug, projectId, payload);
        setProperties((prev) => [...prev, created]);
        notify("custom_properties.toasts.created");
      }
      resetForm();
      setFormOpen(false);
    } catch {
      notify("custom_properties.toasts.save_error", "error");
    } finally {
      setSaving(false);
    }
  };

  const handleToggle = async (property: ICustomProperty, checked: boolean) => {
    if (!workspaceSlug || !projectId || pendingIds.has(property.id)) return;
    markPending(property.id, true);
    try {
      const updated = await service.updateProperty(workspaceSlug, projectId, property.id, { is_active: checked });
      setProperties((prev) => prev.map((item) => (item.id === updated.id ? updated : item)));
    } catch {
      notify("custom_properties.toasts.toggle_error", "error");
    } finally {
      markPending(property.id, false);
    }
  };

  const handleDelete = async () => {
    if (!workspaceSlug || !projectId || !deleteTarget) return;
    setDeleting(true);
    try {
      await service.deleteProperty(workspaceSlug, projectId, deleteTarget.id);
      setProperties((prev) => prev.filter((item) => item.id !== deleteTarget.id));
      notify("custom_properties.toasts.deleted");
      setDeleteTarget(null);
    } catch {
      notify("custom_properties.toasts.delete_error", "error");
    } finally {
      setDeleting(false);
    }
  };

  const handleMove = async (property: ICustomProperty, direction: "up" | "down") => {
    if (!workspaceSlug || !projectId || pendingIds.has(property.id)) return;
    const index = properties.findIndex((item) => item.id === property.id);
    const targetIndex = direction === "up" ? index - 1 : index + 1;
    if (index < 0 || targetIndex < 0 || targetIndex >= properties.length) return;
    const ordered = [...properties];
    const [moved] = ordered.splice(index, 1);
    ordered.splice(targetIndex, 0, moved);
    setProperties(ordered);
    markPending(property.id, true);
    try {
      const updatedList = await service.reorderProperties(
        workspaceSlug,
        projectId,
        ordered.map((item) => item.id)
      );
      setProperties(updatedList);
    } catch {
      setProperties(properties);
      notify("custom_properties.toasts.reorder_error", "error");
    } finally {
      markPending(property.id, false);
    }
  };

  const updateOption = (index: number, patch: Partial<ICustomPropertyOption>) =>
    setDraft((prev) => ({
      ...prev,
      options: prev.options.map((option, i) => (i === index ? { ...option, ...patch } : option)),
    }));

  return (
    <div className="flex flex-col gap-4 border-b border-subtle py-2">
      <div className="flex items-center gap-3">
        <div className="grid size-10 shrink-0 place-items-center rounded-sm bg-layer-2">
          <SlidersHorizontal className="size-4 shrink-0 text-primary" />
        </div>
        <div className="grow">
          <h4 className="text-body-sm-medium text-primary">{t("custom_properties.heading")}</h4>
          <p className="text-caption-md-regular text-secondary">{t("custom_properties.description")}</p>
        </div>
        {!formOpen && (
          <button
            type="button"
            className="flex shrink-0 items-center gap-1.5 rounded-md border border-subtle px-3 py-1.5 text-body-sm-medium text-primary transition-colors hover:bg-layer-2 disabled:opacity-60"
            disabled={!isAdmin || loading}
            onClick={openCreate}
          >
            <Plus className="size-3.5" />
            {t("custom_properties.add_property")}
          </button>
        )}
      </div>

      {formOpen && (
        <div className="flex flex-col gap-3 rounded-md border border-subtle p-4">
          <div className="flex items-center gap-2">
            <label className="flex flex-1 flex-col gap-1">
              <span className="text-caption-md-medium text-secondary">{t("custom_properties.property_name")}</span>
              <Input
                size="md"
                value={draft.name}
                disabled={!isAdmin || saving}
                onChange={(e) => setDraft((prev) => ({ ...prev, name: e.target.value }))}
                placeholder={t("custom_properties.property_name_placeholder")}
              />
            </label>
            <label className="flex flex-1 flex-col gap-1">
              <span className="text-caption-md-medium text-secondary">{t("custom_properties.property_type")}</span>
              <TypeSelect
                value={draft.type}
                disabled={!isAdmin || saving || Boolean(editingId)}
                onChange={(type) => setDraft((prev) => ({ ...prev, type }))}
              />
            </label>
          </div>
          {editingId && (
            <p className="text-caption-md-regular text-tertiary">{t("custom_properties.type_locked_hint")}</p>
          )}

          <label className="flex flex-col gap-1">
            <span className="text-caption-md-medium text-secondary">{t("custom_properties.field_description")}</span>
            <Input
              size="md"
              value={draft.description}
              disabled={!isAdmin || saving}
              onChange={(e) => setDraft((prev) => ({ ...prev, description: e.target.value }))}
              placeholder={t("custom_properties.field_description_placeholder")}
            />
          </label>

          {draft.type === "number" && (
            <label className="flex flex-col gap-1">
              <span className="text-caption-md-medium text-secondary">{t("custom_properties.unit_label")}</span>
              <Input
                size="md"
                value={draft.unit}
                disabled={!isAdmin || saving}
                onChange={(e) => setDraft((prev) => ({ ...prev, unit: e.target.value }))}
                placeholder={t("custom_properties.unit_placeholder")}
              />
            </label>
          )}

          {draft.type === "select" && (
            <div className="flex flex-col gap-2">
              <span className="text-caption-md-medium text-secondary">{t("custom_properties.options_label")}</span>
              {draft.options.map((option, index) => (
                <div key={index} className="flex items-center gap-2">
                  <Popover>
                    <PopoverTrigger
                      render={
                        <button
                          type="button"
                          aria-label={t("custom_properties.options_label")}
                          className="group inline-flex items-center focus:outline-none"
                          disabled={!isAdmin || saving}
                        >
                          <span
                            className="size-6 shrink-0 rounded-md border border-subtle"
                            style={{ backgroundColor: option.color }}
                          />
                        </button>
                      }
                    />
                    <PopoverContent variant="rich" side="bottom" align="start">
                      <div className="w-80 max-w-xs">
                        <ColorSwatchPicker
                          colors={LABEL_COLOR_OPTIONS}
                          value={option.color}
                          onChange={(color) => updateOption(index, { color })}
                        />
                      </div>
                    </PopoverContent>
                  </Popover>
                  <Input
                    size="md"
                    value={option.name}
                    disabled={!isAdmin || saving}
                    onChange={(e) => updateOption(index, { name: e.target.value })}
                    placeholder={t("custom_properties.option_name_placeholder")}
                  />
                  <button
                    type="button"
                    className="grid size-6 shrink-0 place-items-center rounded-md text-tertiary transition-colors hover:bg-layer-2 hover:text-primary disabled:opacity-60"
                    disabled={!isAdmin || saving}
                    aria-label={t("custom_properties.remove_option")}
                    onClick={() => setDraft((prev) => ({ ...prev, options: prev.options.filter((_, i) => i !== index) }))}
                  >
                    <X className="size-3.5" />
                  </button>
                </div>
              ))}
              <button
                type="button"
                className="flex w-fit items-center gap-1.5 rounded-md border border-subtle px-2.5 py-1.5 text-caption-md-medium text-primary transition-colors hover:bg-layer-2 disabled:opacity-60"
                disabled={!isAdmin || saving}
                onClick={() =>
                  setDraft((prev) => ({
                    ...prev,
                    options: [...prev.options, { id: crypto.randomUUID(), name: "", color: getRandomLabelColor() }],
                  }))
                }
              >
                <Plus className="size-3" />
                {t("custom_properties.add_option")}
              </button>
            </div>
          )}

          <div className="flex items-center justify-between">
            <Checkbox
              checked={draft.required}
              disabled={!isAdmin || saving}
              onCheckedChange={(checked) => setDraft((prev) => ({ ...prev, required: checked === true }))}
              label={t("custom_properties.required_toggle")}
            />
            <div className="flex items-center gap-2">
              <button
                type="button"
                className="rounded-md border border-subtle px-3 py-1.5 text-body-sm-medium text-primary transition-colors hover:bg-layer-2 disabled:opacity-60"
                disabled={saving}
                onClick={() => {
                  resetForm();
                  setFormOpen(false);
                }}
              >
                {t("custom_properties.cancel")}
              </button>
              <button
                type="button"
                className="rounded-md bg-primary px-3 py-1.5 text-body-sm-medium text-on-primary transition-opacity hover:opacity-90 disabled:opacity-60"
                disabled={!isAdmin || saving}
                onClick={() => void handleSave()}
              >
                {editingId ? t("custom_properties.save_changes") : t("custom_properties.create_property")}
              </button>
            </div>
          </div>
        </div>
      )}

      <div className="flex flex-col gap-2">
        {properties.map((property, index) => {
          const settings = property.settings_json ?? {};
          return (
            <div key={property.id} className="flex items-center gap-2 rounded-md border border-subtle px-3 py-2">
              <div className="flex flex-col">
                <button
                  type="button"
                  className="grid size-5 place-items-center rounded text-tertiary transition-colors hover:bg-layer-2 hover:text-primary disabled:opacity-40"
                  disabled={!isAdmin || index === 0 || pendingIds.has(property.id)}
                  aria-label={t("custom_properties.move_up")}
                  onClick={() => void handleMove(property, "up")}
                >
                  <ChevronUp className="size-3.5" />
                </button>
                <button
                  type="button"
                  className="grid size-5 place-items-center rounded text-tertiary transition-colors hover:bg-layer-2 hover:text-primary disabled:opacity-40"
                  disabled={!isAdmin || index === properties.length - 1 || pendingIds.has(property.id)}
                  aria-label={t("custom_properties.move_down")}
                  onClick={() => void handleMove(property, "down")}
                >
                  <ChevronDown className="size-3.5" />
                </button>
              </div>
              <div className="grow truncate">
                <div className="flex items-center gap-2">
                  <span className="truncate text-body-sm-medium text-primary">{property.name}</span>
                  {settings.required && (
                    <span className="rounded-sm bg-layer-2 px-1.5 py-0.5 text-caption-md-medium text-secondary">
                      {t("custom_properties.required_badge")}
                    </span>
                  )}
                </div>
                <p className="truncate text-caption-md-regular text-tertiary">
                  {t(`custom_properties.types.${property.type}`)}
                  {property.type === "select" && (settings.options?.length ?? 0) > 0
                    ? ` · ${settings.options?.length} ${t("custom_properties.options_count_suffix")}`
                    : ""}
                  {property.type === "number" && settings.unit ? ` · ${settings.unit}` : ""}
                </p>
              </div>
              <Switch
                size="sm"
                checked={property.is_active}
                aria-label={property.name}
                disabled={!isAdmin || pendingIds.has(property.id)}
                onCheckedChange={(checked) => void handleToggle(property, checked)}
              />
              <button
                type="button"
                className="grid size-6 shrink-0 place-items-center rounded-md text-tertiary transition-colors hover:bg-layer-2 hover:text-primary disabled:opacity-60"
                disabled={!isAdmin}
                aria-label={t("custom_properties.edit_property")}
                onClick={() => openEdit(property)}
              >
                <Pencil className="size-3.5" />
              </button>
              <button
                type="button"
                className="grid size-6 shrink-0 place-items-center rounded-md text-tertiary transition-colors hover:bg-layer-2 hover:text-danger-primary disabled:opacity-60"
                disabled={!isAdmin}
                aria-label={t("custom_properties.delete_property")}
                onClick={() => setDeleteTarget(property)}
              >
                <Trash2 className="size-3.5" />
              </button>
            </div>
          );
        })}
        {!loading && properties.length === 0 && (
          <p className="text-caption-md-regular text-tertiary">{t("custom_properties.empty_state")}</p>
        )}
      </div>

      <ConfirmDialog
        isOpen={Boolean(deleteTarget)}
        handleClose={() => setDeleteTarget(null)}
        handleSubmit={handleDelete}
        isSubmitting={deleting}
        title={t("custom_properties.delete_modal.title")}
        content={
          <>
            {t("custom_properties.delete_modal.content_prefix")}{" "}
            <span className="font-medium text-primary">{deleteTarget?.name}</span>
            {t("custom_properties.delete_modal.content_suffix")}
          </>
        }
      />
    </div>
  );
}
