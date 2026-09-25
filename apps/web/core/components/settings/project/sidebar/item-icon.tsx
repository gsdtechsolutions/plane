/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { LayoutTemplate } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import {
  CyclesOutline,
  EstimateOutline,
  IntakeOutline,
  LabelsOutline,
  MembersOutline,
  ModuleOutline,
  PagesOutline,
  ServerOutline,
  StateOutline,
  TriggerOutline,
  ViewsOutline,
} from "@makeplane/propel/icons";
import { SlidersHorizontal } from "lucide-react";
// plane imports
import type { ISvgIcons } from "@plane/blocks/icons";
import type { TProjectSettingsTabs } from "@plane/types";
// components
import { SettingIcon } from "@/components/icons/attachment";

export const PROJECT_SETTINGS_ICONS: Record<TProjectSettingsTabs, LucideIcon | React.FC<ISvgIcons>> = {
  general: SettingIcon,
  members: MembersOutline,
  features_cycles: CyclesOutline,
  features_modules: ModuleOutline,
  features_views: ViewsOutline,
  features_pages: PagesOutline,
  features_intake: IntakeOutline,
  states: StateOutline,
  labels: LabelsOutline,
  estimates: EstimateOutline,
  automations: TriggerOutline,
templates: LayoutTemplate,
custom_properties: SlidersHorizontal,
infra: ServerOutline,
};
