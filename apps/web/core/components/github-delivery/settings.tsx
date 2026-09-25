/** Copyright (c) 2023-present Plane Software, Inc. and contributors. SPDX-License-Identifier: AGPL-3.0-only */
import { useState } from "react";
import { observer } from "mobx-react";
import useSWR from "swr";
import { Building2, Github, RefreshCw, User } from "lucide-react";
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
  const [mode, setMode] = useState<"idle" | "personal" | "enterprise">("idle");
  const [enterpriseUrl, setEnterpriseUrl] = useState("");
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
      setMode("idle");
    } finally {
      setPending(false);
    }
  };
  const startConnect = (accountType: "personal" | "enterprise") =>
    void run(async () => {
      const result = await service.connect(workspaceSlug, {
        account_type: accountType,
        enterprise_url: accountType === "enterprise" ? enterpriseUrl : undefined,
      });
      // The next page registers a GitHub App from a prefilled manifest and
      // returns automatically; every credential is created by GitHub itself.
      window.location.assign(result.url);
    });
  const selectClass = "w-full rounded-md border border-subtle bg-surface-1 px-3 py-2 text-13";
  const hasActiveConnection = data?.connections.some((connection) => connection.active);
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
              Connect GitHub to track pull requests and releases alongside your work. Connecting creates a private
              GitHub App scoped to this board — read-only, and only for repositories you explicitly connect.
            </p>
          </div>
        </div>
        {!hasActiveConnection && mode === "idle" && (
          <Button
            size="sm"
            stretch="auto"
            variant="primary"
            disabled={pending || isLoading}
            onClick={() => setMode("personal")}
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
      {mode !== "idle" && (
        <div className="space-y-4 rounded-lg border border-subtle bg-surface-2 p-4">
          <div className="flex flex-wrap gap-3" role="radiogroup" aria-label="GitHub account type">
            {(
              [
                ["personal", User, "Personal account", "github.com — creates the App under your account"],
                ["enterprise", Building2, "GitHub Enterprise", "your own GitHub Enterprise Server"],
              ] as const
            ).map(([value, Icon, title, hint]) => (
              <button
                key={value}
                type="button"
                role="radio"
                aria-checked={mode === value}
                disabled={pending}
                onClick={() => setMode(value)}
                className={`flex min-w-52 flex-1 items-start gap-3 rounded-lg border p-3 text-left text-13 transition-colors ${
                  mode === value ? "border-accent-strong bg-surface-1" : "border-subtle hover:bg-surface-1"
                }`}
              >
                <Icon className="mt-0.5 size-4 shrink-0" aria-hidden />
                <span>
                  <span className="block font-medium">{title}</span>
                  <span className="mt-0.5 block text-12 text-secondary">{hint}</span>
                </span>
              </button>
            ))}
          </div>
          {mode === "enterprise" && (
            <label className="block space-y-1 text-13">
              <span>GitHub Enterprise Server address</span>
              <input
                type="url"
                className={selectClass}
                placeholder="https://github.example.com"
                value={enterpriseUrl}
                disabled={pending}
                onChange={(event) => setEnterpriseUrl(event.target.value)}
              />
            </label>
          )}
          <p className="text-12 text-secondary">
            You will be asked to create a GitHub App named after this workspace. GitHub generates every credential —
            nothing is stored on this board in plain text. You choose the repositories after the App is installed.
          </p>
          <div className="flex gap-2">
            <Button
              size="sm"
              stretch="auto"
              variant="primary"
              disabled={pending || (mode === "enterprise" && enterpriseUrl.trim().length === 0)}
              onClick={() => startConnect(mode)}
              label={mode === "enterprise" ? "Continue to GitHub Enterprise" : "Continue to GitHub"}
            />
            <Button
              size="sm"
              stretch="auto"
              variant="secondary"
              disabled={pending}
              onClick={() => setMode("idle")}
              label="Cancel"
            />
          </div>
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
              {connection.host_display}
              {connection.app_slug ? ` · App ${connection.app_slug}` : ""}
              {connection.active ? " · Connected · read access only" : " · Disconnected · history retained"}
            </p>
          </div>
          <div className="flex gap-2">
            {!connection.active && (
              <Button
                size="sm"
                stretch="auto"
                variant="secondary"
                disabled={pending}
                onClick={() => setMode("personal")}
                label="Reconnect"
              />
            )}
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
                    setMode("idle");
                  }, "GitHub disconnected. Linked development history is still available.")
                }
                label="Disconnect"
              />
            )}
          </div>
        </div>
      ))}
      {hasActiveConnection && (
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
                {data?.connections
                  .filter((item) => item.active)
                  .map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.account} ({item.host_display})
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
