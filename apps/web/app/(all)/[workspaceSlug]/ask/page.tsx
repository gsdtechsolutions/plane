/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// components
import { PageHead } from "@/components/core/page-title";
import { AskPanel } from "@/components/ai-ops/ask-panel";
import { useParams } from "react-router";

export default function WorkspaceAskPage() {
  const { workspaceSlug } = useParams();
  if (!workspaceSlug) return null;
  return (
    <>
      <PageHead title="Ask" />
      <AskPanel workspaceSlug={workspaceSlug} />
    </>
  );
}
