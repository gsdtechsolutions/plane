/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

/**
 * Fork feature: work item templates (per-project) settings section.
 * Local state only — no mobx store needed (mirrors the board-rules pattern on
 * the automations page). List + inline create/edit form + set-default/delete.
 */

import { useEffect, useMemo, useState } from "react";
import { useParams } from "next/navigation";
import { Copy, Pencil, Plus, Star, Trash2 } from "lucide-react";
// plane imports
import { EUserPermissions, EUserPermissionsLevel } from "@plane/constants";
import { Select } from "@plane/blocks/select";
import { setToast } from "@plane/blocks/toast";
import { useTranslation } from "@plane/i18n";
import type { IIssueLabel, IState } from "@plane/types";
// services
import {
  IssueTemplateService,
  type IIssueTemplate,
  type TIssueTemplatePriority,
} from "@/services/templates/issue-template.service";
import { IssueLabelService } from "@/services/issue/issue_label.service";
import { ProjectStateService } from "@/services/project/project-state.service";
import { ProjectMemberService } from "@/services/project/project-member.service";
import { WorkspaceService } from "@/services/workspace.service";
// hooks
import { useUserPermissions } from "@/hooks/store/user";

type TemplateOption = { value: string; label: string };

const EMPTY_OPTION: TemplateOption = { value: "", label: "" };

const PRIORITIES: TIssueTemplatePriority[] = ["urgent", "high", "medium", "low", "none"];

const DEFAULT_DESCRIPTION_HTML = "<p></p>";

type TemplateDraft = {
  id: string | null;
  name: string;
  description_html: string;
  priority: TIssueTemplatePriority;
  state: string | null;
  labels: string[];
  due_in_days: string;
  is_default: boolean;
};

const EMPTY_DRAFT: TemplateDraft = {
  id: null,
  name: "",
  description_html: DEFAULT_DESCRIPTION_HTML,
  priority: "none",
  state: null,
  labels: [],
  due_in_days: "",
  is_default: false,
};

function TemplateSelect(props: {
  onChange: (option: TemplateOption) => void;
  options: TemplateOption[];
  placeholder: string;
  value: TemplateOption;
  disabled?: boolean;
}) {
  const { onChange, options, placeholder, value, disabled } = props;
  return (
    <Select<TemplateOption>
      getValues={() => options}
      value={value.value ? value : EMPTY_OPTION}
      onChange={(selectedValue) => {
        // Select emits the raw option value; map it back to the option object.
        const selected = options.find((option) => option.value === selectedValue);
        if (selected) onChange(selected);
      }}
      getOptionValue={(option) => option.value}
      getOptionLabel={(option) => option.label}
      placeholder={placeholder}
      disabled={disabled}
      showSearch={false}
      pinSelected={false}
      contentSizing="anchor"
    >
      <Select.Trigger<TemplateOption> variant="select-md" className="w-44 max-w-full">
        <span className="min-w-0 grow truncate text-left">{value.value ? value.label : placeholder}</span>
      </Select.Trigger>
    </Select>
  );
}

export function IssueTemplateSettings() {
  // router
  const { workspaceSlug, projectId } = useParams();
  const { t } = useTranslation();
  const loadErrorMessage = t("project_settings.templates.toasts.load_error");
  const { allowPermissions } = useUserPermissions();

  const isAdmin = allowPermissions([EUserPermissions.ADMIN], EUserPermissionsLevel.PROJECT);

  // services (stateless, safe to construct once per render)
  const services = useMemo(
    () => ({
      templates: new IssueTemplateService(),
      states: new ProjectStateService(),
      labels: new IssueLabelService(),
      members: new WorkspaceService(),
      projectMembers: new ProjectMemberService(),
    }),
    []
  );

  // data
  const [loading, setLoading] = useState(true);
  const [templates, setTemplates] = useState<IIssueTemplate[]>([]);
  const [states, setStates] = useState<IState[]>([]);
  const [labels, setLabels] = useState<IIssueLabel[]>([]);
  const [pendingTemplateIds, setPendingTemplateIds] = useState<Set<string>>(new Set());

  // create/edit form
  const [formOpen, setFormOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [draft, setDraft] = useState<TemplateDraft>(EMPTY_DRAFT);

  useEffect(() => {
    if (!workspaceSlug || !projectId) return;
    let cancelled = false;
    setLoading(true);
    const fetchData = async () => {
      try {
        const [templatesData, statesData, labelsData] = await Promise.all([
          services.templates.listTemplates(workspaceSlug, projectId),
          services.states.getStates(workspaceSlug, projectId),
          services.labels.getProjectLabels(workspaceSlug, projectId),
        ]);
        if (cancelled) return;
        setTemplates(templatesData);
        setStates(statesData);
        setLabels(labelsData);
      } catch {
        if (!cancelled)
          setToast({
            type: "error",
            title: "Error!",
            message: loadErrorMessage,
          });
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void fetchData();
    return () => {
      cancelled = true;
    };
  }, [workspaceSlug, projectId, services, loadErrorMessage]);

  // option lists
  const stateOptions: TemplateOption[] = useMemo(
    () => states.map((state) => ({ value: state.id, label: state.name })),
    [states]
  );
  const labelOptions: TemplateOption[] = useMemo(
    () => labels.map((label) => ({ value: label.id, label: label.name })),
    [labels]
  );
  const priorityOptions: TemplateOption[] = PRIORITIES.map((priority) => ({
    value: priority,
    label: t(`project_settings.automations.board_rules.priorities.${priority}`),
  }));

  const notify = (messageKey: string, type: "success" | "error" = "success") =>
    setToast({
      type,
      title: type === "success" ? "Success!" : "Error!",
      message: t(messageKey),
    });

  const resetForm = () => setDraft(EMPTY_DRAFT);

  const openCreate = () => {
    resetForm();
    setFormOpen(true);
  };

  const openEdit = (template: IIssueTemplate) => {
    setDraft({
      id: template.id,
      name: template.name,
      description_html: template.description_html || DEFAULT_DESCRIPTION_HTML,
      priority: template.priority,
      state: template.state,
      labels: template.labels ?? [],
      due_in_days: template.due_in_days === null || template.due_in_days === undefined ? "" : String(template.due_in_days),
      is_default: template.is_default,
    });
    setFormOpen(true);
  };

  const toggleDraftLabel = (labelId: string) =>
    setDraft((prev) => ({
      ...prev,
      labels: prev.labels.includes(labelId) ? prev.labels.filter((id) => id !== labelId) : [...prev.labels, labelId],
    }));

  const draftPayload = () => ({
    name: draft.name.trim(),
    description_html: draft.description_html?.trim() ? draft.description_html : DEFAULT_DESCRIPTION_HTML,
    priority: draft.priority,
    state: draft.state ?? null,
    labels: draft.labels,
    due_in_days: draft.due_in_days.trim() === "" ? null : Number(draft.due_in_days),
    is_default: draft.is_default,
  });

  const handleSave = async () => {
    if (!workspaceSlug || !projectId) return;
    if (!draft.name.trim()) {
      notify("project_settings.templates.toasts.validation", "error");
      return;
    }
    setSaving(true);
    try {
      if (draft.id) {
        const updated = await services.templates.updateTemplate(workspaceSlug, projectId, draft.id, draftPayload());
        setTemplates((prev) => prev.map((item) => (item.id === updated.id ? updated : item)));
        notify("project_settings.templates.toasts.updated");
      } else {
        const created = await services.templates.createTemplate(workspaceSlug, projectId, draftPayload());
        setTemplates((prev) => [created, ...prev]);
        notify("project_settings.templates.toasts.created");
      }
      resetForm();
      setFormOpen(false);
    } catch {
      notify(draft.id ? "project_settings.templates.toasts.update_error" : "project_settings.templates.toasts.save_error", "error");
    } finally {
      setSaving(false);
    }
  };

  const markPending = (templateId: string, pending: boolean) =>
    setPendingTemplateIds((prev) => {
      const next = new Set(prev);
      if (pending) next.add(templateId);
      else next.delete(templateId);
      return next;
    });

  const handleSetDefault = async (template: IIssueTemplate) => {
    if (!workspaceSlug || !projectId || template.is_default || pendingTemplateIds.has(template.id)) return;
    markPending(template.id, true);
    try {
      const updated = await services.templates.updateTemplate(workspaceSlug, projectId, template.id, {
        is_default: true,
      });
      // The backend demotes every other default for the project; mirror that locally.
      setTemplates((prev) => prev.map((item) => (item.id === updated.id ? updated : { ...item, is_default: false })));
      notify("project_settings.templates.toasts.default_set");
    } catch {
      notify("project_settings.templates.toasts.toggle_error", "error");
    } finally {
      markPending(template.id, false);
    }
  };

  const handleDelete = async (template: IIssueTemplate) => {
    if (!workspaceSlug || !projectId) return;
    try {
      await services.templates.deleteTemplate(workspaceSlug, projectId, template.id);
      setTemplates((prev) => prev.filter((item) => item.id !== template.id));
      notify("project_settings.templates.toasts.deleted");
    } catch {
      notify("project_settings.templates.toasts.delete_error", "error");
    }
  };

  const summarizeTemplate = (template: IIssueTemplate) => {
    const parts: string[] = [];
    if (template.priority !== "none")
      parts.push(t(`project_settings.automations.board_rules.priorities.${template.priority}`));
    const stateName = stateOptions.find((option) => option.value === template.state)?.label;
    if (stateName) parts.push(stateName);
    if (template.labels?.length)
      parts.push(
        template.labels
          .map((labelId) => labelOptions.find((option) => option.value === labelId)?.label ?? labelId)
          .join(", ")
      );
    if (template.due_in_days !== null && template.due_in_days !== undefined)
      parts.push(t("project_settings.templates.due_in_days_short", { days: template.due_in_days }));
    return parts.length ? parts.join(" · ") : t("project_settings.templates.no_prefill");
  };

  return (
    <div className="flex flex-col gap-4 border-b border-subtle py-2">
      <div className="flex items-center gap-3">
        <div className="grid size-10 shrink-0 place-items-center rounded-sm bg-layer-2">
          <Copy className="size-4 shrink-0 text-primary" />
        </div>
        <div className="grow">
          <h4 className="text-body-sm-medium text-primary">{t("project_settings.templates.heading")}</h4>
          <p className="text-caption-md-regular text-secondary">
            {t("project_settings.templates.work_items_description")}
          </p>
        </div>
        {!formOpen && (
          <button
            type="button"
            className="flex shrink-0 items-center gap-1.5 rounded-md border border-subtle px-3 py-1.5 text-body-sm-medium text-primary transition-colors hover:bg-layer-2 disabled:opacity-60"
            disabled={!isAdmin || loading}
            onClick={openCreate}
          >
            <Plus className="size-3.5" />
            {t("project_settings.templates.add_template")}
          </button>
        )}
      </div>

      {formOpen && (
        <div className="flex flex-col gap-3 rounded-md border border-subtle p-4">
          {/* name */}
          <label className="flex flex-col gap-1">
            <span className="text-caption-md-medium text-secondary">{t("project_settings.templates.name")}</span>
            <input
              type="text"
              className="rounded-md border border-subtle bg-layer-2 px-2.5 py-1.5 text-body-sm-regular text-primary placeholder:text-secondary"
              placeholder={t("project_settings.templates.name_placeholder")}
              value={draft.name}
              disabled={!isAdmin || saving}
              onChange={(event) => setDraft((prev) => ({ ...prev, name: event.target.value }))}
            />
          </label>

          {/* description */}
          <label className="flex flex-col gap-1">
            <span className="text-caption-md-medium text-secondary">{t("project_settings.templates.description_field")}</span>
            <textarea
              className="min-h-24 rounded-md border border-subtle bg-layer-2 px-2.5 py-1.5 text-body-sm-regular text-primary placeholder:text-secondary"
              placeholder={t("project_settings.templates.description_placeholder")}
              value={draft.description_html === DEFAULT_DESCRIPTION_HTML ? "" : draft.description_html}
              disabled={!isAdmin || saving}
              onChange={(event) => setDraft((prev) => ({ ...prev, description_html: event.target.value }))}
            />
            <span className="text-caption-md-regular text-tertiary">{t("project_settings.templates.description_hint")}</span>
          </label>

          {/* priority + state + due in days */}
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-caption-md-medium text-secondary">{t("project_settings.templates.prefill")}</span>
            <TemplateSelect
              options={priorityOptions}
              value={priorityOptions.find((option) => option.value === draft.priority) ?? EMPTY_OPTION}
              placeholder={t("project_settings.templates.select_placeholder")}
              disabled={!isAdmin || saving}
              onChange={(option) => setDraft((prev) => ({ ...prev, priority: option.value as TIssueTemplatePriority }))}
            />
            <TemplateSelect
              options={stateOptions}
              value={stateOptions.find((option) => option.value === draft.state) ?? EMPTY_OPTION}
              placeholder={t("project_settings.templates.state_placeholder")}
              disabled={!isAdmin || saving}
              onChange={(option) => setDraft((prev) => ({ ...prev, state: option.value }))}
            />
            <input
              type="number"
              min={0}
              className="w-28 rounded-md border border-subtle bg-layer-2 px-2.5 py-1.5 text-body-sm-regular text-primary placeholder:text-secondary"
              placeholder={t("project_settings.templates.due_in_days_placeholder")}
              value={draft.due_in_days}
              disabled={!isAdmin || saving}
              onChange={(event) => setDraft((prev) => ({ ...prev, due_in_days: event.target.value }))}
            />
          </div>

          {/* labels (multi-select chips) */}
          {labelOptions.length > 0 && (
            <div className="flex flex-col gap-1.5">
              <span className="text-caption-md-medium text-secondary">{t("project_settings.templates.labels")}</span>
              <div className="flex flex-wrap gap-1.5">
                {labelOptions.map((label) => {
                  const selected = draft.labels.includes(label.value);
                  return (
                    <button
                      key={label.value}
                      type="button"
                      disabled={!isAdmin || saving}
                      onClick={() => toggleDraftLabel(label.value)}
                      className={`rounded-full border px-2.5 py-1 text-caption-md-regular transition-colors disabled:opacity-60 ${
                        selected
                          ? "border-transparent bg-accent-primary/20 text-primary"
                          : "border-subtle text-secondary hover:bg-layer-2"
                      }`}
                    >
                      {label.label}
                    </button>
                  );
                })}
              </div>
            </div>
          )}

          {/* default toggle + form controls */}
          <div className="flex flex-wrap items-center justify-between gap-2">
            <label className="flex items-center gap-1.5 text-caption-md-regular text-secondary">
              <input
                type="checkbox"
                checked={draft.is_default}
                disabled={!isAdmin || saving}
                onChange={(event) => setDraft((prev) => ({ ...prev, is_default: event.target.checked }))}
              />
              {t("project_settings.templates.set_default")}
            </label>
            <div className="flex items-center gap-2">
              <button
                type="button"
                className="bg-primary text-on-primary rounded-md px-3 py-1.5 text-body-sm-medium disabled:opacity-60"
                disabled={!isAdmin || saving}
                onClick={() => void handleSave()}
              >
                {draft.id ? t("project_settings.templates.save") : t("project_settings.templates.create")}
              </button>
              <button
                type="button"
                className="rounded-md px-3 py-1.5 text-body-sm-medium text-secondary transition-colors hover:bg-layer-2 disabled:opacity-60"
                disabled={saving}
                onClick={() => {
                  resetForm();
                  setFormOpen(false);
                }}
              >
                {t("project_settings.templates.cancel")}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* templates list */}
      <div className="flex flex-col gap-2">
        {loading ? (
          <p className="text-caption-md-regular text-secondary">{t("project_settings.templates.loading")}</p>
        ) : templates.length === 0 ? (
          <p className="text-caption-md-regular text-secondary">{t("project_settings.templates.empty_state")}</p>
        ) : (
          templates.map((template) => (
            <div key={template.id} className="flex items-center gap-3 rounded-md border border-subtle px-3 py-2.5">
              <div className="min-w-0 grow">
                <p className="flex items-center gap-1.5 truncate text-body-sm-medium text-primary">
                  {template.name}
                  {template.is_default && (
                    <span className="rounded-sm bg-layer-2 px-1.5 py-0.5 text-caption-md-regular text-secondary">
                      {t("project_settings.templates.default_badge")}
                    </span>
                  )}
                </p>
                <p className="truncate text-caption-md-regular text-secondary">{summarizeTemplate(template)}</p>
              </div>
              <button
                type="button"
                className={`grid size-6 shrink-0 place-items-center rounded-md transition-colors hover:bg-layer-2 disabled:opacity-60 ${
                  template.is_default ? "text-accent-primary" : "text-secondary"
                }`}
                disabled={!isAdmin || template.is_default || pendingTemplateIds.has(template.id)}
                onClick={() => void handleSetDefault(template)}
                aria-label={t("project_settings.templates.set_default")}
                title={t("project_settings.templates.set_default")}
              >
                <Star className={`size-3.5 ${template.is_default ? "fill-current" : ""}`} />
              </button>
              <button
                type="button"
                className="grid size-6 shrink-0 place-items-center rounded-md text-secondary transition-colors hover:bg-layer-2 disabled:opacity-60"
                disabled={!isAdmin || pendingTemplateIds.has(template.id)}
                onClick={() => openEdit(template)}
                aria-label={t("project_settings.templates.edit")}
              >
                <Pencil className="size-3.5" />
              </button>
              <button
                type="button"
                className="grid size-6 shrink-0 place-items-center rounded-md text-secondary transition-colors hover:bg-layer-2 hover:text-danger-primary disabled:opacity-60"
                disabled={!isAdmin || pendingTemplateIds.has(template.id)}
                onClick={() => void handleDelete(template)}
                aria-label={t("project_settings.templates.delete")}
              >
                <Trash2 className="size-3.5" />
              </button>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
