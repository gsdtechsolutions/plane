/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

/**
 * Fork feature: template picker for the create-work-item modal.
 * Lists the project's work item templates (+ "Blank"); selection prefills the
 * form via the issue-modal context (workItemTemplateId -> handleTemplateChange).
 */

import { useEffect, useMemo, useState } from "react";
import { useParams } from "next/navigation";
// plane imports
import { useTranslation } from "@plane/i18n";
// services
import { IssueTemplateService, type IIssueTemplate } from "@/services/templates/issue-template.service";

type PickerOption = { value: string; label: string };

export function WorkItemTemplatePicker(props: {
  projectId: string;
  onSelect: (template: IIssueTemplate | null) => void;
}) {
  const { projectId, onSelect } = props;
  // router
  const { workspaceSlug } = useParams();
  const { t } = useTranslation();

  // state
  const [templates, setTemplates] = useState<IIssueTemplate[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [selectedId, setSelectedId] = useState<string>("");
  const [service] = useState(() => new IssueTemplateService());

  // load project templates once; hide the picker entirely when none exist
  useEffect(() => {
    if (!workspaceSlug || !projectId) return;
    let cancelled = false;
    service
      .listTemplates(workspaceSlug, projectId)
      .then((data) => {
        if (!cancelled) {
          setTemplates(data);
          setLoaded(true);
        }
      })
      .catch(() => {
        if (!cancelled) setLoaded(true);
      });
    return () => {
      cancelled = true;
    };
  }, [workspaceSlug, projectId, service]);

  const options: PickerOption[] = useMemo(
    () => [
      { value: "", label: t("project_settings.templates.picker.blank") },
      ...templates.map((template) => ({
        value: template.id,
        label: template.is_default ? `${template.name} ★` : template.name,
      })),
    ],
    [templates, t]
  );

  if (!loaded || templates.length === 0) return null;

  return (
    <label className="flex shrink-0 items-center gap-1.5">
      <span className="whitespace-nowrap text-caption-md-regular text-secondary">
        {t("project_settings.templates.picker.label")}
      </span>
      <select
        className="rounded-md border border-subtle bg-layer-2 px-2 py-1 text-caption-md-regular text-primary"
        value={selectedId}
        onChange={(event) => {
          const id = event.target.value;
          setSelectedId(id);
          onSelect(templates.find((template) => template.id === id) ?? null);
        }}
      >
        {options.map((option) => (
          <option key={option.value || "blank"} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </label>
  );
}
