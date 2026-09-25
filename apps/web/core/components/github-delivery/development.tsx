/** Copyright (c) 2023-present Plane Software, Inc. and contributors. SPDX-License-Identifier: AGPL-3.0-only */
import { useState } from "react";
import { Link } from "react-router";
import useSWR from "swr";
import { GitPullRequest, Github, ExternalLink } from "lucide-react";
import { Button } from "@makeplane/propel/components/button";
import { githubDeliveryService as service, githubError } from "@/services/integrations/github-delivery.service";
import type { GithubPullRequest } from "@/services/integrations/github-delivery.service";

function PullRequestRow({
  pullRequest,
  onUnlink,
  pending,
}: {
  pullRequest: GithubPullRequest;
  onUnlink?: () => void;
  pending?: boolean;
}) {
  const status = pullRequest.draft
    ? "Draft"
    : pullRequest.state === "merged"
      ? "Merged"
      : pullRequest.state === "closed"
        ? "Closed"
        : "Open";
  return (
    <li className="flex items-start gap-3 border-b border-subtle py-3 last:border-0">
      <GitPullRequest className="mt-0.5 size-4 shrink-0 text-secondary" aria-hidden />
      <div className="min-w-0 flex-1">
        <a
          href={pullRequest.url}
          target="_blank"
          rel="noopener noreferrer"
          className="text-13 font-medium break-words hover:underline"
        >
          {pullRequest.title || `Pull request #${pullRequest.number}`}
          <ExternalLink className="ml-1 inline size-3" aria-hidden />
        </a>
        <p className="mt-1 text-12 break-words text-secondary">
          {pullRequest.repository} #{pullRequest.number} · {status}
          {pullRequest.review_state !== "pending"
            ? ` · Latest review: ${pullRequest.review_state.replaceAll("_", " ")}`
            : ""}
          {!pullRequest.connected ? " · Disconnected" : ""}
        </p>
      </div>
      {onUnlink && (
        <Button
          size="sm"
          stretch="auto"
          variant="secondary"
          disabled={pending}
          aria-label={`Unlink pull request #${pullRequest.number}`}
          onClick={onUnlink}
          label="Unlink"
        />
      )}
    </li>
  );
}

export function ProjectDevelopment({ workspaceSlug, projectId }: { workspaceSlug: string; projectId: string }) {
  const { data, error, isLoading, mutate } = useSWR(
    ["github-project-development", workspaceSlug, projectId],
    () => service.development(workspaceSlug, projectId),
    { refreshInterval: 30000 }
  );
  return (
    <section className="h-full overflow-y-auto px-5 py-6 md:px-8">
      <div className="mx-auto max-w-5xl space-y-6">
        <header className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h1 className="text-24 font-semibold">Development</h1>
            <p className="mt-1 text-13 text-secondary">Pull requests and GitHub releases connected to this project.</p>
          </div>
          <Button size="sm" stretch="auto" variant="secondary" onClick={() => void mutate()} label="Refresh" />
        </header>
        {isLoading && (
          <p role="status" className="text-13 text-secondary">
            Loading development activity…
          </p>
        )}
        {error && (
          <div role="alert" className="rounded-lg border border-subtle p-4 text-13">
            {githubError(error)}{" "}
            <Button size="sm" stretch="auto" variant="secondary" onClick={() => void mutate()} label="Try again" />
          </div>
        )}
        {data && data.repositories.length === 0 && (
          <div className="rounded-xl border border-subtle p-8 text-center">
            <Github className="mx-auto size-8 text-secondary" aria-hidden />
            <h2 className="mt-4 text-18 font-medium">Bring your code into the picture</h2>
            <p className="mx-auto mt-2 max-w-md text-13 text-secondary">
              A workspace administrator can connect a GitHub repository. Pull requests that mention this project’s work
              item keys will then appear on the matching work items.
            </p>
            <Link
              to={`/${workspaceSlug}/settings/integrations/`}
              className="mt-5 inline-block text-13 font-medium text-accent-primary hover:underline"
            >
              Open integration settings
            </Link>
          </div>
        )}
        {data && data.repositories.length > 0 && (
          <>
            <div className="flex flex-wrap gap-2">
              {data.repositories.map((repository) => (
                <span key={repository.id} className="rounded-md border border-subtle px-3 py-1.5 text-12">
                  {repository.repository}
                  {repository.active
                    ? repository.sync_status === "pending"
                      ? " · Sync queued"
                      : repository.sync_status === "failed"
                        ? " · Sync needs attention"
                        : ""
                    : " · Disconnected"}
                </span>
              ))}
            </div>
            <p className="text-12 text-secondary">
              Merged pull requests are development evidence. Publish a release in Releases when the work is ready for
              your customers.
            </p>
            <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">
              <section className="rounded-xl border border-subtle p-5" aria-labelledby="development-pulls">
                <h2 id="development-pulls" className="text-16 font-semibold">
                  Pull requests <span className="font-normal text-12 text-secondary">{data.pull_requests.length}</span>
                </h2>
                {data.pull_requests.length ? (
                  <ul className="mt-2">
                    {data.pull_requests.map((pr) => (
                      <PullRequestRow key={pr.id} pullRequest={pr} />
                    ))}
                  </ul>
                ) : (
                  <p className="py-6 text-13 text-secondary">
                    No pull requests have synced yet. New activity will appear here after GitHub sends an update.
                  </p>
                )}
              </section>
              <section className="rounded-xl border border-subtle p-5" aria-labelledby="development-releases">
                <h2 id="development-releases" className="text-16 font-semibold">
                  GitHub releases
                </h2>
                {data.releases.length ? (
                  <ul className="mt-2">
                    {data.releases.map((release) => (
                      <li key={release.id} className="border-b border-subtle py-3 last:border-0">
                        <a
                          href={release.url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-13 font-medium break-words hover:underline"
                        >
                          {release.name || release.tag_name}
                          <ExternalLink className="ml-1 inline size-3" aria-hidden />
                        </a>
                        <p className="mt-1 text-12 break-words text-secondary">
                          {release.repository} · {release.tag_name}
                          {release.draft ? " · Draft" : release.prerelease ? " · Prerelease" : ""}
                        </p>
                        {release.published_at && (
                          <time dateTime={release.published_at} className="text-12 text-secondary">
                            {new Date(release.published_at).toLocaleDateString()}
                          </time>
                        )}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="py-6 text-13 text-secondary">No GitHub releases have synced yet.</p>
                )}
              </section>
            </div>
            <p className="text-12 text-secondary">
              Showing recent activity, up to 200 pull requests and releases. Initial sync includes the latest 100 of
              each.
            </p>
          </>
        )}
      </div>
    </section>
  );
}

export function IssueDevelopment({
  workspaceSlug,
  projectId,
  issueId,
  disabled,
}: {
  workspaceSlug: string;
  projectId: string;
  issueId: string;
  disabled: boolean;
}) {
  const { data, error, isLoading, mutate } = useSWR(
    ["github-issue-development", workspaceSlug, projectId, issueId],
    () => service.issuePullRequests(workspaceSlug, projectId, issueId)
  );
  const [url, setUrl] = useState("");
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState("");
  const unlink = async (id: string) => {
    setPending(true);
    setMessage("");
    try {
      await service.unlink(workspaceSlug, projectId, issueId, id);
      await mutate();
    } catch (cause) {
      setMessage(githubError(cause));
    } finally {
      setPending(false);
    }
  };
  // Project guests do not have access to private development metadata.
  if ((error as { response?: { status?: number } })?.response?.status === 403) return null;
  return (
    <details className="rounded-lg border border-subtle p-3">
      <summary className="cursor-pointer text-13 font-medium">
        Development{data?.length ? ` (${data.length})` : ""}
      </summary>
      {isLoading && (
        <p role="status" className="mt-3 text-12 text-secondary">
          Loading linked pull requests…
        </p>
      )}
      {error && (
        <p role="alert" className="mt-3 text-12">
          {githubError(error)}
        </p>
      )}
      {data && (
        <ul>
          {data.map((pr) => (
            <PullRequestRow
              key={pr.id}
              pullRequest={pr}
              pending={pending}
              onUnlink={disabled ? undefined : () => void unlink(pr.id)}
            />
          ))}
        </ul>
      )}
      {data?.length === 0 && (
        <p className="mt-3 text-12 text-secondary">
          No linked pull requests. Mention this work item’s key in a pull request title, description or branch, or link
          one below.
        </p>
      )}
      {!disabled && (
        <form
          className="mt-3 flex flex-wrap items-end gap-2"
          onSubmit={async (event) => {
            event.preventDefault();
            setPending(true);
            setMessage("");
            try {
              const links = await service.link(workspaceSlug, projectId, issueId, url.trim());
              await mutate(links, false);
              setUrl("");
            } catch (cause) {
              setMessage(githubError(cause));
            } finally {
              setPending(false);
            }
          }}
        >
          <label className="min-w-0 flex-1 space-y-1 text-12">
            <span>GitHub pull request URL</span>
            <input
              type="url"
              required
              value={url}
              disabled={pending}
              onChange={(event) => setUrl(event.target.value)}
              placeholder="https://github.com/owner/repo/pull/123"
              className="w-full rounded-md border border-subtle bg-surface-1 px-2 py-1.5 text-12"
            />
          </label>
          <Button
            size="sm"
            stretch="auto"
            type="submit"
            variant="secondary"
            disabled={pending || !url.trim()}
            label="Link"
          />
        </form>
      )}
      {message && (
        <p role="status" className="mt-2 text-12">
          {message}
        </p>
      )}
    </details>
  );
}
