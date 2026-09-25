/** Copyright (c) 2023-present Plane Software, Inc. and contributors. SPDX-License-Identifier: AGPL-3.0-only */
import { useState } from "react";
import { observer } from "mobx-react";
import useSWR from "swr";
import { Github, RefreshCw } from "lucide-react";
import { Button } from "@makeplane/propel/components/button";
import { useProject } from "@/hooks/store/use-project";
import { githubDeliveryService as service, githubError } from "@/services/integrations/github-delivery.service";

export const GithubDeliverySettings = observer(function GithubDeliverySettings({
  workspaceSlug,
}: {
  workspaceSlug: string;
}) {
  const { joinedProjectIds, getProjectById } = useProject();
  const { data, error, mutate, isLoading } = useSWR(
    ["github-delivery-status", workspaceSlug],
    () => service.status(workspaceSlug),
    {
      refreshInterval: (value) => (value?.mappings.some((mapping) => mapping.sync_status === "pending") ? 10000 : 0),
    }
  );
  const [connectionId, setConnectionId] = useState("");
  const [repositoryId, setRepositoryId] = useState("");
  const [projectId, setProjectId] = useState("");
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState("");
  const {
    data: repositories,
    error: repositoriesError,
    isLoading: loadingRepositories,
  } = useSWR(connectionId ? ["github-delivery-repositories", workspaceSlug, connectionId] : null, () =>
    service.repositories(workspaceSlug, connectionId)
  );
  const projects = joinedProjectIds
    .map((id) => getProjectById(id))
    .filter((project) => project && (project.member_role ?? 0) >= 15);
  const run = async (action: () => Promise<unknown>, success = "") => {
    setPending(true);
    setMessage("");
    try {
      await action();
      await mutate();
      setMessage(success);
    } catch (cause) {
      setMessage(githubError(cause));
    } finally {
      setPending(false);
    }
  };
  const selectClass = "w-full rounded-md border border-subtle bg-surface-1 px-3 py-2 text-13";
  return (
    <section aria-labelledby="github-delivery-heading" className="space-y-5 border-b border-subtle py-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex gap-3">
          <Github className="mt-0.5 size-6 shrink-0" aria-hidden />
          <div>
            <h4 id="github-delivery-heading" className="text-16 font-semibold">
              GitHub
            </h4>
            <p className="mt-1 max-w-xl text-13 text-secondary">
              Connect selected repositories to track pull requests and releases alongside your work.
            </p>
          </div>
        </div>
        {data?.configured && (
          <Button
            size="sm"
            stretch="auto"
            variant="primary"
            disabled={pending}
            onClick={() =>
              void run(async () => {
                const result = await service.connect(workspaceSlug);
                window.location.assign(result.url);
              })
            }
            label="Connect GitHub"
          />
        )}
      </div>
      {isLoading && (
        <p role="status" className="text-13 text-secondary">
          Loading GitHub connections…
        </p>
      )}
      {error && (
        <div role="alert" className="text-13">
          <p>{githubError(error)}</p>
          <Button size="sm" stretch="auto" variant="secondary" onClick={() => void mutate()} label="Try again" />
        </div>
      )}
      {data && !data.configured && (
        <div className="rounded-lg border border-subtle bg-surface-2 p-4 text-13">
          <p className="font-medium">GitHub setup is not complete</p>
          <p className="mt-1 text-secondary">
            An instance administrator needs to configure a GitHub App before you can connect your account. No
            repositories are connected automatically.
          </p>
          <details className="mt-3">
            <summary className="cursor-pointer font-medium">Setup details for your administrator</summary>
            <p className="mt-2 text-secondary">
              Grant read access to Metadata, Pull requests and Contents. Subscribe to Pull request, Pull request review,
              Release, Installation and Installation repositories events. Leave “Request user authorization during
              installation” off; this connection verifies your account in a separate authorization step.
            </p>
            {data.missing_settings.length > 0 && (
              <p className="mt-2 break-words">Missing settings: {data.missing_settings.join(", ")}</p>
            )}
            {data.configuration_error && <p className="mt-2">{data.configuration_error}</p>}
            <dl className="mt-2 space-y-2 break-all">
              {[
                ["Setup URL", data.setup_url],
                ["Callback URL", data.callback_url],
                ["Webhook URL", data.webhook_url],
              ].map(
                ([label, url]) =>
                  url && (
                    <div key={label}>
                      <dt className="font-medium">{label}</dt>
                      <dd>{url}</dd>
                    </div>
                  )
              )}
            </dl>
          </details>
        </div>
      )}
      {message && (
        <p role="status" className="text-13">
          {message}
        </p>
      )}
      {data?.connections.map((connection) => (
        <div
          key={connection.id}
          className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-subtle p-4"
        >
          <div>
            <p className="text-14 font-medium">{connection.account}</p>
            <p className="text-12 text-secondary">
              {connection.active ? "Connected · read access only" : "Disconnected · history retained"}
            </p>
          </div>
          {connection.active && (
            <Button
              size="sm"
              stretch="auto"
              variant="secondary"
              disabled={pending}
              onClick={() =>
                void run(async () => {
                  await service.disconnect(workspaceSlug, connection.id);
                  if (connectionId === connection.id) {
                    setConnectionId("");
                    setRepositoryId("");
                  }
                }, "GitHub disconnected. Linked development history is still available.")
              }
              label="Disconnect"
            />
          )}
        </div>
      ))}
      {data?.configured && data.connections.some((connection) => connection.active) && (
        <form
          className="space-y-3 rounded-lg border border-subtle p-4"
          onSubmit={(event) => {
            event.preventDefault();
            void run(async () => {
              await service.map(workspaceSlug, {
                connection_id: connectionId,
                repository_id: Number(repositoryId),
                project_id: projectId,
              });
              setRepositoryId("");
            }, "Repository connected. Recent pull requests and releases are syncing.");
          }}
        >
          <h5 className="text-14 font-medium">Connect a repository to a project</h5>
          <div className="grid gap-3 md:grid-cols-3">
            <label className="space-y-1 text-13">
              <span>GitHub account</span>
              <select
                className={selectClass}
                value={connectionId}
                required
                disabled={pending}
                onChange={(event) => {
                  setConnectionId(event.target.value);
                  setRepositoryId("");
                }}
              >
                <option value="">Choose an account</option>
                {data.connections
                  .filter((item) => item.active)
                  .map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.account}
                    </option>
                  ))}
              </select>
            </label>
            <label className="space-y-1 text-13">
              <span>Repository</span>
              <select
                className={selectClass}
                value={repositoryId}
                required
                disabled={pending || !repositories}
                onChange={(event) => setRepositoryId(event.target.value)}
              >
                <option value="">{loadingRepositories ? "Loading repositories…" : "Choose a repository"}</option>
                {repositories?.map((repo) => (
                  <option key={repo.id} value={repo.id}>
                    {repo.full_name}
                    {repo.private ? " (private)" : ""}
                  </option>
                ))}
              </select>
            </label>
            <label className="space-y-1 text-13">
              <span>Project</span>
              <select
                className={selectClass}
                value={projectId}
                required
                disabled={pending}
                onChange={(event) => setProjectId(event.target.value)}
              >
                <option value="">Choose a project</option>
                {projects.map(
                  (project) =>
                    project && (
                      <option key={project.id} value={project.id}>
                        {project.name}
                      </option>
                    )
                )}
              </select>
            </label>
          </div>
          {repositoriesError && (
            <p role="alert" className="text-13">
              {githubError(repositoriesError)}
            </p>
          )}
          {repositories?.length === 0 && (
            <p className="text-13 text-secondary">
              No authorized repositories are available. Reconnect GitHub after updating the App’s repository access.
            </p>
          )}
          <p className="text-12 text-secondary">
            Only repositories you authorize are available. Each repository connects to one project at a time.
          </p>
          <Button
            size="sm"
            stretch="auto"
            variant="primary"
            type="submit"
            disabled={pending || !connectionId || !repositoryId || !projectId}
            label="Connect repository"
          />
        </form>
      )}
      {data && data.mappings.length > 0 && (
        <div className="space-y-2">
          <h5 className="text-14 font-medium">Connected repositories</h5>
          {data.mappings.map((mapping) => (
            <div
              key={mapping.id}
              className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-subtle p-3"
            >
              <div className="min-w-0">
                <p className="text-13 font-medium break-words">
                  {mapping.repository} → {getProjectById(mapping.project_id)?.name ?? "Project"}
                </p>
                <p className="text-12 text-secondary">
                  {mapping.sync_status === "pending"
                    ? "Sync queued"
                    : mapping.sync_status === "failed"
                      ? mapping.sync_error
                      : "Synced"}
                </p>
              </div>
              <div className="flex gap-2">
                <Button
                  size="sm"
                  stretch="auto"
                  variant="secondary"
                  disabled={pending}
                  onClick={() => void run(() => service.sync(workspaceSlug, mapping.id), "Sync queued.")}
                  icon={<RefreshCw className="size-3.5" aria-hidden />}
                  label="Sync"
                />
                <Button
                  size="sm"
                  stretch="auto"
                  variant="secondary"
                  disabled={pending}
                  onClick={() =>
                    void run(
                      () => service.unmap(workspaceSlug, mapping.id),
                      "Repository disconnected. History retained."
                    )
                  }
                  label="Disconnect repository"
                />
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
});
