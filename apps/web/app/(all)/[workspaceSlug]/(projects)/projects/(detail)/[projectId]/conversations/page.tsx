/** Copyright (c) 2023-present Plane Software, Inc. and contributors. SPDX-License-Identifier: AGPL-3.0-only */
import { useParams } from "react-router";
import { PageHead } from "@/components/core/page-title";
import { ProjectConversations } from "@/components/slack-delivery/conversations";

export default function ConversationsPage() {
  const { workspaceSlug, projectId } = useParams();
  if (!workspaceSlug || !projectId) return null;
  return (
    <>
      <PageHead title="Conversations" />
      <ProjectConversations workspaceSlug={workspaceSlug} projectId={projectId} />
    </>
  );
}
