/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

/**
 * Fork feature: Board rules (per-project WHEN trigger THEN actions) on the
 * project automations settings page. Local state only — no mobx store needed.
 */

import { useEffect, useMemo, useState } from "react";
import { useParams } from "next/navigation";
import { Plus, Trash2, Workflow } from "lucide-react";
// plane imports
import { EUserPermissions, EUserPermissionsLevel } from "@plane/constants";
import { Select } from "@plane/blocks/select";
import { setToast } from "@plane/blocks/toast";
import { useTranslation } from "@plane/i18n";
import { Switch } from "@makeplane/propel/components/switch";
import type { IIssueLabel, IState } from "@plane/types";
// services
import {
  AutomationRuleService,
  type IAutomationRule,
  type IAutomationRuleAction,
  type TAutomationActionType,
  type TAutomationTriggerType,
} from "@/services/automation/automation-rule.service";
import { IssueLabelService } from "@/services/issue/issue_label.service";
import { ProjectStateService } from "@/services/project/project-state.service";
import { ProjectMemberService } from "@/services/project/project-member.service";
import { WorkspaceService } from "@/services/workspace.service";
// hooks
import { useUserPermissions } from "@/hooks/store/user";

type RuleOption = { value: string; label: string };

type RuleActionDraft = IAutomationRuleAction;

const EMPTY_OPTION: RuleOption = { value: "", label: "" };

const TRIGGER_TYPE_KEYS: Record<TAutomationTriggerType, string> = {
  state_changed: "project_settings.automations.board_rules.triggers.state_changed",
  assignee_added: "project_settings.automations.board_rules.triggers.assignee_added",
};

function RuleSelect(props: {
  onChange: (option: RuleOption) => void;
  options: RuleOption[];
  placeholder: string;
  value: RuleOption;
}) {
  const { onChange, options, placeholder, value } = props;
  return (
    <Select<RuleOption>
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
      showSearch={false}
      pinSelected={false}
      contentSizing="anchor"
    >
      <Select.Trigger<RuleOption> variant="select-md" className="w-44 max-w-full">
        <span className="min-w-0 grow truncate text-left">{value.value ? value.label : placeholder}</span>
      </Select.Trigger>
    </Select>
  );
}

export function BoardRulesAutomation() {
  // router
  const { workspaceSlug, projectId } = useParams();
  const { t } = useTranslation();
  const { allowPermissions } = useUserPermissions();

  const isAdmin = allowPermissions([EUserPermissions.ADMIN], EUserPermissionsLevel.PROJECT);

  // services (stateless, safe to construct once per render)
  const services = useMemo(
    () => ({
      rules: new AutomationRuleService(),
      states: new ProjectStateService(),
      members: new WorkspaceService(),
      projectMembers: new ProjectMemberService(),
      labels: new IssueLabelService(),
    }),
    []
  );

  // data
  const [loading, setLoading] = useState(true);
  const [rules, setRules] = useState<IAutomationRule[]>([]);
  const [pendingRuleIds, setPendingRuleIds] = useState<Set<string>>(new Set());
  const [states, setStates] = useState<IState[]>([]);
  const [memberOptions, setMemberOptions] = useState<RuleOption[]>([]);
  const [labels, setLabels] = useState<IIssueLabel[]>([]);

  // add-rule form
  const [formOpen, setFormOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [ruleName, setRuleName] = useState("");
  const [triggerType, setTriggerType] = useState<TAutomationTriggerType>("state_changed");
  const [triggerValue, setTriggerValue] = useState<string>("");
  const [actions, setActions] = useState<RuleActionDraft[]>([{ type: "set_state", value: "" }]);

  useEffect(() => {
    if (!workspaceSlug || !projectId) return;
    let cancelled = false;
    setLoading(true);
    const fetchData = async () => {
      try {
        const [rulesData, statesData, membersData, labelsData, projectMembersData] = await Promise.all([
          services.rules.listRules(workspaceSlug, projectId),
          services.states.getStates(workspaceSlug, projectId),
          services.members.fetchWorkspaceMembers(workspaceSlug),
          services.labels.getProjectLabels(workspaceSlug, projectId),
          services.projectMembers.fetchProjectMembers(workspaceSlug, projectId),
        ]);
        if (cancelled) return;
        setRules(rulesData);
        setStates(statesData);
        setLabels(labelsData);
        const eligibleMemberIds = new Set(
          projectMembersData
            .filter((membership) => membership.role >= EUserPermissions.MEMBER)
            .map((membership) => membership.member)
        );
        setMemberOptions(
          membersData
            .filter((membership) => eligibleMemberIds.has(membership.member.id))
            .map((membership) => ({
              value: membership.member.id,
              label:
                membership.member.display_name ||
                `${membership.member.first_name} ${membership.member.last_name}`.trim() ||
                membership.member.email ||
                membership.member.id,
            }))
        );
      } catch {
        if (!cancelled)
          setToast({
            type: "error",
            title: "Error!",
            message: t("project_settings.automations.board_rules.toasts.load_error"),
          });
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void fetchData();
    return () => {
      cancelled = true;
    };
  }, [workspaceSlug, projectId, services, t]);

  // option lists
  const stateOptions: RuleOption[] = useMemo(
    () => states.map((state) => ({ value: state.id, label: state.name })),
    [states]
  );
  const labelOptions: RuleOption[] = useMemo(
    () => labels.map((label) => ({ value: label.id, label: label.name })),
    [labels]
  );
  const triggerValueOptions = [
    { value: "", label: t("project_settings.automations.board_rules.any") },
    ...(triggerType === "state_changed" ? stateOptions : memberOptions),
  ];

  const actionTypeOptions: RuleOption[] = (
    [
      "set_state",
      "set_priority",
      "add_label",
      "remove_label",
      "assign_member",
      "set_due_date",
    ] as TAutomationActionType[]
  ).map((type) => ({
    value: type,
    label: t(`project_settings.automations.board_rules.actions.${type}`),
  }));

  const priorityOptions: RuleOption[] = ["urgent", "high", "medium", "low", "none"].map((priority) => ({
    value: priority,
    label: t(`project_settings.automations.board_rules.priorities.${priority}`),
  }));

  // lookups for summaries
  const stateName = (id: string) => stateOptions.find((option) => option.value === id)?.label ?? id;
  const memberName = (id: string) => memberOptions.find((option) => option.value === id)?.label ?? id;
  const labelName = (id: string) => labelOptions.find((option) => option.value === id)?.label ?? id;

  const summarizeTrigger = (rule: IAutomationRule) => {
    const valueLabel = !rule.trigger_value
      ? t("project_settings.automations.board_rules.any")
      : rule.trigger_type === "state_changed"
        ? stateName(rule.trigger_value)
        : memberName(rule.trigger_value);
    return `${t(TRIGGER_TYPE_KEYS[rule.trigger_type])} ${valueLabel}`;
  };

  const actionValueLabel = (action: IAutomationRuleAction) => {
    if (!action.value) return "";
    switch (action.type) {
      case "set_state":
        return stateName(action.value);
      case "assign_member":
        return memberName(action.value);
      case "add_label":
      case "remove_label":
        return labelName(action.value);
      case "set_priority":
        return t(`project_settings.automations.board_rules.priorities.${action.value}`);
      case "set_due_date":
        return action.value;
      default:
        return action.value;
    }
  };

  const summarizeActions = (rule: IAutomationRule) =>
    (rule.actions ?? []).map((action) => {
      const typeLabel = t(`project_settings.automations.board_rules.actions.${action.type}`);
      const valueLabel = actionValueLabel(action);
      return valueLabel ? `${typeLabel}: ${valueLabel}` : typeLabel;
    });

  // form helpers
  const resetForm = () => {
    setRuleName("");
    setTriggerType("state_changed");
    setTriggerValue("");
    setActions([{ type: "set_state", value: "" }]);
  };

  const updateAction = (index: number, patch: Partial<RuleActionDraft>) =>
    setActions((prev) => prev.map((action, i) => (i === index ? { ...action, ...patch } : action)));

  const notify = (messageKey: string, type: "success" | "error" = "success") =>
    setToast({
      type,
      title: type === "success" ? "Success!" : "Error!",
      message: t(messageKey),
    });

  const handleToggle = async (rule: IAutomationRule, checked: boolean) => {
    if (!workspaceSlug || !projectId || pendingRuleIds.has(rule.id)) return;
    setPendingRuleIds((prev) => new Set(prev).add(rule.id));
    try {
      const updated = await services.rules.updateRule(workspaceSlug, projectId, rule.id, { is_active: checked });
      setRules((prev) => prev.map((item) => (item.id === updated.id ? updated : item)));
    } catch {
      notify("project_settings.automations.board_rules.toasts.toggle_error", "error");
    } finally {
      setPendingRuleIds((prev) => {
        const next = new Set(prev);
        next.delete(rule.id);
        return next;
      });
    }
  };

  const handleDelete = async (rule: IAutomationRule) => {
    if (!workspaceSlug || !projectId) return;
    try {
      await services.rules.deleteRule(workspaceSlug, projectId, rule.id);
      setRules((prev) => prev.filter((item) => item.id !== rule.id));
      notify("project_settings.automations.board_rules.toasts.deleted");
    } catch {
      notify("project_settings.automations.board_rules.toasts.delete_error", "error");
    }
  };

  const handleSave = async () => {
    if (!workspaceSlug || !projectId) return;
    const trimmedName = ruleName.trim();
    const filledActions = actions.filter((action) => action.value);
    if (!trimmedName || filledActions.length !== actions.length || filledActions.length === 0) {
      notify("project_settings.automations.board_rules.toasts.validation", "error");
      return;
    }
    setSaving(true);
    try {
      const created = await services.rules.createRule(workspaceSlug, projectId, {
        name: trimmedName,
        trigger_type: triggerType,
        trigger_value: triggerValue || null,
        actions: filledActions.map((action) => ({ type: action.type, value: action.value })),
        is_active: true,
      });
      setRules((prev) => [created, ...prev]);
      resetForm();
      setFormOpen(false);
      notify("project_settings.automations.board_rules.toasts.created");
    } catch {
      notify("project_settings.automations.board_rules.toasts.save_error", "error");
    } finally {
      setSaving(false);
    }
  };

  const renderActionValueControl = (action: RuleActionDraft, index: number) => {
    switch (action.type) {
      case "set_state":
        return (
          <RuleSelect
            options={stateOptions}
            value={stateOptions.find((option) => option.value === action.value) ?? EMPTY_OPTION}
            placeholder={t("project_settings.automations.board_rules.select_placeholder")}
            onChange={(option) => updateAction(index, { value: option.value })}
          />
        );
      case "assign_member":
        return (
          <RuleSelect
            options={memberOptions}
            value={memberOptions.find((option) => option.value === action.value) ?? EMPTY_OPTION}
            placeholder={t("project_settings.automations.board_rules.select_placeholder")}
            onChange={(option) => updateAction(index, { value: option.value })}
          />
        );
      case "add_label":
      case "remove_label":
        return (
          <RuleSelect
            options={labelOptions}
            value={labelOptions.find((option) => option.value === action.value) ?? EMPTY_OPTION}
            placeholder={t("project_settings.automations.board_rules.select_placeholder")}
            onChange={(option) => updateAction(index, { value: option.value })}
          />
        );
      case "set_priority":
        return (
          <RuleSelect
            options={priorityOptions}
            value={priorityOptions.find((option) => option.value === action.value) ?? EMPTY_OPTION}
            placeholder={t("project_settings.automations.board_rules.select_placeholder")}
            onChange={(option) => updateAction(index, { value: option.value })}
          />
        );
      case "set_due_date":
        return (
          <input
            type="date"
            className="rounded-md border border-subtle bg-layer-2 px-2.5 py-1.5 text-body-sm-regular text-primary"
            value={action.value ?? ""}
            disabled={!isAdmin}
            onChange={(event) => updateAction(index, { value: event.target.value })}
          />
        );
      default:
        return null;
    }
  };

  return (
    <div className="flex flex-col gap-4 border-b border-subtle py-2">
      <div className="flex items-center gap-3">
        <div className="grid size-10 shrink-0 place-items-center rounded-sm bg-layer-2">
          <Workflow className="size-4 shrink-0 text-primary" />
        </div>
        <div className="grow">
          <h4 className="text-body-sm-medium text-primary">{t("project_settings.automations.board_rules.heading")}</h4>
          <p className="text-caption-md-regular text-secondary">
            {t("project_settings.automations.board_rules.description")}
          </p>
        </div>
        {!formOpen && (
          <button
            type="button"
            className="flex shrink-0 items-center gap-1.5 rounded-md border border-subtle px-3 py-1.5 text-body-sm-medium text-primary transition-colors hover:bg-layer-2 disabled:opacity-60"
            disabled={!isAdmin || loading}
            onClick={() => {
              resetForm();
              setFormOpen(true);
            }}
          >
            <Plus className="size-3.5" />
            {t("project_settings.automations.board_rules.add_rule")}
          </button>
        )}
      </div>

      {formOpen && (
        <div className="flex flex-col gap-3 rounded-md border border-subtle p-4">
          {/* name */}
          <label className="flex flex-col gap-1">
            <span className="text-caption-md-medium text-secondary">
              {t("project_settings.automations.board_rules.rule_name")}
            </span>
            <input
              type="text"
              className="rounded-md border border-subtle bg-layer-2 px-2.5 py-1.5 text-body-sm-regular text-primary placeholder:text-secondary"
              placeholder={t("project_settings.automations.board_rules.rule_name_placeholder")}
              value={ruleName}
              disabled={!isAdmin || saving}
              onChange={(event) => setRuleName(event.target.value)}
            />
          </label>

          {/* trigger */}
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-caption-md-medium text-secondary">
              {t("project_settings.automations.board_rules.trigger")}
            </span>
            <RuleSelect
              options={(Object.keys(TRIGGER_TYPE_KEYS) as TAutomationTriggerType[]).map((type) => ({
                value: type,
                label: t(TRIGGER_TYPE_KEYS[type]),
              }))}
              value={{ value: triggerType, label: t(TRIGGER_TYPE_KEYS[triggerType]) }}
              placeholder={t("project_settings.automations.board_rules.select_placeholder")}
              onChange={(option) => {
                setTriggerType(option.value as TAutomationTriggerType);
                setTriggerValue("");
              }}
            />
            <RuleSelect
              options={triggerValueOptions}
              value={triggerValueOptions.find((option) => option.value === triggerValue) ?? EMPTY_OPTION}
              placeholder={t("project_settings.automations.board_rules.any")}
              onChange={(option) => setTriggerValue(option.value)}
            />
          </div>

          {/* actions */}
          <div className="flex flex-col gap-2">
            <span className="text-caption-md-medium text-secondary">
              {t("project_settings.automations.board_rules.actions.label")}
            </span>
            {actions.map((action, index) => (
              <div key={index} className="flex flex-wrap items-center gap-2">
                <RuleSelect
                  options={actionTypeOptions}
                  value={actionTypeOptions.find((option) => option.value === action.type) ?? EMPTY_OPTION}
                  placeholder={t("project_settings.automations.board_rules.select_placeholder")}
                  onChange={(option) => updateAction(index, { type: option.value as TAutomationActionType, value: "" })}
                />
                {renderActionValueControl(action, index)}
                {actions.length > 1 && (
                  <button
                    type="button"
                    className="grid size-6 shrink-0 place-items-center rounded-md text-secondary transition-colors hover:bg-layer-2 hover:text-danger-primary"
                    disabled={!isAdmin || saving}
                    onClick={() => setActions((prev) => prev.filter((_, i) => i !== index))}
                    aria-label={t("project_settings.automations.board_rules.actions.remove")}
                  >
                    <Trash2 className="size-3.5" />
                  </button>
                )}
              </div>
            ))}
            <button
              type="button"
              className="flex w-fit items-center gap-1.5 rounded-md text-caption-md-medium text-primary transition-colors hover:bg-layer-2 disabled:opacity-60"
              disabled={!isAdmin || saving}
              onClick={() => setActions((prev) => [...prev, { type: "set_state", value: "" }])}
            >
              <Plus className="size-3.5" />
              {t("project_settings.automations.board_rules.actions.add")}
            </button>
          </div>

          {/* form controls */}
          <div className="flex items-center gap-2">
            <button
              type="button"
              className="bg-primary text-on-primary rounded-md px-3 py-1.5 text-body-sm-medium disabled:opacity-60"
              disabled={!isAdmin || saving}
              onClick={() => void handleSave()}
            >
              {t("project_settings.automations.board_rules.save")}
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
              {t("project_settings.automations.board_rules.cancel")}
            </button>
          </div>
        </div>
      )}

      {/* rules list */}
      <div className="flex flex-col gap-2">
        {loading ? (
          <p className="text-caption-md-regular text-secondary">
            {t("project_settings.automations.board_rules.loading")}
          </p>
        ) : rules.length === 0 ? (
          <p className="text-caption-md-regular text-secondary">
            {t("project_settings.automations.board_rules.empty_state")}
          </p>
        ) : (
          rules.map((rule) => (
            <div key={rule.id} className="flex items-center gap-3 rounded-md border border-subtle px-3 py-2.5">
              <div className="min-w-0 grow">
                <p className="truncate text-body-sm-medium text-primary">{rule.name}</p>
                <p className="truncate text-caption-md-regular text-secondary">
                  {summarizeTrigger(rule)} → {summarizeActions(rule).join(" → ")}
                </p>
              </div>
              <button
                type="button"
                className="grid size-6 shrink-0 place-items-center rounded-md text-secondary transition-colors hover:bg-layer-2 hover:text-danger-primary disabled:opacity-60"
                disabled={!isAdmin || pendingRuleIds.has(rule.id)}
                onClick={() => void handleDelete(rule)}
                aria-label={t("project_settings.automations.board_rules.delete")}
              >
                <Trash2 className="size-3.5" />
              </button>
              <Switch
                size="sm"
                checked={rule.is_active}
                aria-label={rule.name}
                disabled={!isAdmin || pendingRuleIds.has(rule.id)}
                onCheckedChange={(checked) => void handleToggle(rule, checked)}
              />
            </div>
          ))
        )}
      </div>
    </div>
  );
}
