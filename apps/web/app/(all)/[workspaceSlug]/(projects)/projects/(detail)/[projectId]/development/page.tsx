/** Copyright (c) 2023-present Plane Software, Inc. and contributors. SPDX-License-Identifier: AGPL-3.0-only */
import { useParams } from "react-router";
import { PageHead } from "@/components/core/page-title";
import { ProjectDevelopment } from "@/components/github-delivery/development";

export default function DevelopmentPage() {
  const { workspaceSlug, projectId } = useParams();
  if (!workspaceSlug || !projectId) return null;
  return (
    <>
      <PageHead title="Development" />
      <ProjectDevelopment workspaceSlug={workspaceSlug} projectId={projectId} />
    </>
  );
}
