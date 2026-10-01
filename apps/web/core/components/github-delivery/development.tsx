/** Copyright (c) 2023-present Plane Software, Inc. and contributors. SPDX-License-Identifier: AGPL-3.0-only */
import { useEffect, useState } from "react";
import { Link } from "react-router";
import { observer } from "mobx-react";
import useSWR from "swr";
import {
  CheckCircle2,
  CircleDot,
  ExternalLink,
  GitCommit,
  GitMerge,
  GitPullRequest,
  Github,
  History,
  Loader2,
  MessageSquare,
  ThumbsDown,
  ThumbsUp,
  Trash2,
  XCircle,
} from "lucide-react";
import { Avatar } from "@makeplane/propel/components/avatar";
import { Button } from "@makeplane/propel/components/button";
import { Collapsible } from "@makeplane/propel/components/collapsible";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogHeader,
  DialogHeading,
  DialogMain,
  DialogTitle,
} from "@makeplane/propel/components/dialog";
import { Switch } from "@makeplane/propel/components/switch";
import { getFileURL } from "@plane/utils";
import { SidebarSectionCard } from "@/components/common/layout/sidebar/section-card";
// hooks
import { useMember } from "@/hooks/store/use-member";
import { useProject } from "@/hooks/store/use-project";
import { useProjectState } from "@/hooks/store/use-project-state";
import { githubDeliveryService as service, githubError } from "@/services/integrations/github-delivery.service";
import { MemberSelect } from "@/components/dropdowns/member/member-select";
import type {
  GithubAutomationRule,
  GithubCheckCounts,
  GithubCommit,
  GithubPullRequest,
  GithubTimelineEvent,
} from "@/services/integrations/github-delivery.service";

function prStateStyle(pullRequest: GithubPullRequest) {
  if (pullRequest.state === "merged") return { label: "Merged", dot: "bg-custom-primary-200", text: "text-purple-500" };
  if (pullRequest.state === "closed") return { label: "Closed", dot: "bg-red-500", text: "text-red-500" };
  if (pullRequest.draft) return { label: "Draft", dot: "bg-secondary", text: "text-secondary" };
  return { label: "Open", dot: "bg-green-500", text: "text-green-500" };
}

function MetaRow({ children }: { children: React.ReactNode }) {
  return <p className="mt-0.5 truncate text-12 text-tertiary">{children}</p>;
}

const NO_CHECKS: GithubCheckCounts = { total: 0, failed: 0, pending: 0 };

function ChecksBadge({ checks }: { checks: GithubCheckCounts }) {
  const { total, failed, pending } = checks ?? NO_CHECKS;
  if (total === 0) return null;
  const passing = total - failed - pending;
  if (failed > 0) {
    return (
      <span
        className="text-red-500 inline-flex shrink-0 items-center gap-1 text-12"
        title={`${failed} of ${total} checks failed`}
      >
        <XCircle className="size-3.5" aria-hidden />
        {passing}/{total}
      </span>
    );
  }
  if (pending > 0) {
    return (
      <span
        className="inline-flex shrink-0 items-center gap-1 text-12 text-secondary"
        title={`${pending} of ${total} checks still running`}
      >
        <Loader2 className="size-3.5 animate-spin" aria-hidden />
        {passing}/{total}
      </span>
    );
  }
  return (
    <span className="text-green-500 inline-flex shrink-0 items-center gap-1 text-12" title="All checks passed">
      <CheckCircle2 className="size-3.5" aria-hidden />
      {total}/{total}
    </span>
  );
}

/** Member avatar when the GitHub identity resolved to a board member, else the plain text author. */
const AuthorBadge = observer(function AuthorBadge({ userId, fallback }: { userId: string | null; fallback: string }) {
  const { getUserDetails } = useMember();
  const member = userId ? getUserDetails(userId) : undefined;
  if (!member) return fallback ? <span className="truncate text-12 text-tertiary">{fallback}</span> : null;
  return (
    <Avatar
      alt={member.display_name ?? "Member"}
      fallback={member.display_name?.[0]?.toUpperCase()}
      src={getFileURL(member.avatar_url ?? "")}
      size="xs"
      tooltip={member.display_name ?? true}
    />
  );
});

function PullRequestRow({ pullRequest }: { pullRequest: GithubPullRequest }) {
  const state = prStateStyle(pullRequest);
  return (
    <li className="group flex items-center gap-2 py-2">
      <span className={`size-1.5 shrink-0 rounded-full ${state.dot}`} aria-hidden />
      <GitPullRequest className={`size-3.5 shrink-0 ${state.text}`} aria-hidden />
      <a
        href={pullRequest.url}
        target="_blank"
        rel="noopener noreferrer"
        className="min-w-0 flex-1 truncate text-13 hover:underline"
        title={pullRequest.title || `Pull request #${pullRequest.number}`}
      >
        <span className={`font-medium ${state.text}`}>#{pullRequest.number}</span> {pullRequest.title || "Pull request"}
      </a>
      <ChecksBadge checks={pullRequest.checks} />
      <span className="hidden shrink-0 text-12 text-tertiary sm:inline">{state.label}</span>
      <ExternalLink
        className="size-3 shrink-0 text-tertiary opacity-0 transition-opacity group-hover:opacity-100"
        aria-hidden
      />
    </li>
  );
}

function CommitRow({ commit }: { commit: GithubCommit }) {
  return (
    <li className="group flex items-center gap-2 py-2">
      <GitCommit className="size-3.5 shrink-0 text-secondary" aria-hidden />
      <a
        href={commit.url}
        target="_blank"
        rel="noopener noreferrer"
        className="min-w-0 flex-1 truncate text-13 hover:underline"
        title={commit.message}
      >
        <span className="text-custom-primary-100 font-medium">{commit.short_sha}</span>{" "}
        {commit.message.split("\n")[0] || "Commit"}
      </a>
      <span className="hidden shrink-0 sm:inline">
        <AuthorBadge userId={commit.author_user_id} fallback={commit.author} />
      </span>
      <ExternalLink
        className="size-3 shrink-0 text-tertiary opacity-0 transition-opacity group-hover:opacity-100"
        aria-hidden
      />
    </li>
  );
}

const DevelopmentAutomationCard = observer(function DevelopmentAutomationCard({
  workspaceSlug,
  projectId,
}: {
  workspaceSlug: string;
  projectId: string;
}) {
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [draftOpen, setDraftOpen] = useState(false);
  // store hooks
  const { getProjectById } = useProject();
  const { fetchProjectStates, getProjectStates } = useProjectState();
  const isAdmin = (getProjectById(projectId)?.member_role ?? 0) >= 15;
  const { data, error, isLoading, mutate } = useSWR(["github-delivery-automation", workspaceSlug, projectId], () =>
    service.automationRules(workspaceSlug, projectId)
  );
  useEffect(() => {
    void fetchProjectStates(workspaceSlug, projectId);
  }, [workspaceSlug, projectId, fetchProjectStates]);
  // Guests do not have access to project development settings.
  if ((error as { response?: { status?: number } })?.response?.status === 403) return null;
  const states = getProjectStates(projectId) ?? [];
  const rules = data?.rules ?? [];

  const run = async (action: () => Promise<unknown>, note = "Rule saved.") => {
    setSaving(true);
    setMessage("");
    try {
      await action();
      await mutate();
      setMessage(note);
    } catch (cause) {
      setMessage(githubError(cause));
    } finally {
      setSaving(false);
    }
  };
  const patchRule = (rule: GithubAutomationRule, changes: Partial<GithubAutomationRule>, note = "Rule saved.") =>
    run(() => service.updateAutomationRule(workspaceSlug, projectId, rule.id, { ...rule, ...changes }), note);
  const createRule = (draft: Partial<GithubAutomationRule>) =>
    run(async () => {
      await service.createAutomationRule(workspaceSlug, projectId, {
        enabled: true,
        require_all_merged: true,
        ...draft,
      });
      setDraftOpen(false);
    }, "Rule added.");
  const removeRule = (rule: GithubAutomationRule) =>
    run(() => service.deleteAutomationRule(workspaceSlug, projectId, rule.id), "Rule removed.");

  const stateSelect = (
    value: string | null,
    onPick: (stateId: string | null) => void,
    disabled: boolean,
    idPrefix: string
  ) => (
    <select
      id={idPrefix}
      className="w-full max-w-xs rounded-md border border-subtle bg-surface-1 px-3 py-2 text-13"
      value={value ?? ""}
      disabled={disabled}
      onChange={(event) => onPick(event.target.value || null)}
    >
      <option value="">No state change</option>
      {states.map((state) => (
        <option key={state.id} value={state.id}>
          {state.name}
        </option>
      ))}
    </select>
  );

  return (
    <section className="rounded-xl border border-subtle p-5" aria-labelledby="development-automation">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 id="development-automation" className="text-16 font-semibold">
            Automation
          </h2>
          <p className="mt-1 max-w-xl text-13 text-secondary">
            When a pull request linked to a work item merges into a branch, move the work item to a state and assign it
            — every action optional, every rule toggleable.
          </p>
        </div>
        {isAdmin && data && (
          <Button
            size="sm"
            stretch="auto"
            variant="secondary"
            disabled={saving || draftOpen}
            onClick={() => setDraftOpen(true)}
            label="Add rule"
          />
        )}
      </div>

      {rules.length === 0 && !draftOpen && !isLoading && (
        <p className="mt-4 text-13 text-secondary">
          No automation rules yet. Add one — for example: merged into <span className="font-medium">main</span> → move
          to <span className="font-medium">Done</span> and assign the release owner.
        </p>
      )}

      <ul className="mt-4 space-y-3">
        {rules.map((rule) => (
          <li key={rule.id} className="rounded-lg border border-subtle p-4">
            <div className="flex flex-wrap items-center gap-3">
              <Switch
                size="sm"
                checked={rule.enabled}
                disabled={!isAdmin || saving}
                onCheckedChange={(checked) => patchRule(rule, { enabled: checked })}
                aria-label="Toggle rule"
              />
              <label className="flex min-w-56 flex-1 items-center gap-2 text-13">
                <span className="shrink-0 text-secondary">When a pull request merges into</span>
                <input
                  type="text"
                  defaultValue={rule.base_branch}
                  disabled={!isAdmin || saving}
                  placeholder="any branch"
                  spellCheck={false}
                  className="min-w-32 flex-1 rounded-md border border-subtle bg-surface-1 px-2.5 py-1.5 text-13"
                  onBlur={(event) => {
                    const next = event.target.value.trim();
                    if (next !== rule.base_branch) patchRule(rule, { base_branch: next });
                  }}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") event.currentTarget.blur();
                  }}
                />
              </label>
              {isAdmin && (
                <Button
                  size="sm"
                  stretch="auto"
                  variant="ghost"
                  disabled={saving}
                  onClick={() => removeRule(rule)}
                  aria-label="Delete rule"
                  icon={<Trash2 className="size-3.5" aria-hidden />}
                  label="Delete"
                />
              )}
            </div>
            <div className="mt-3 grid gap-3 md:grid-cols-2">
              <label className="block space-y-1 text-13">
                <span className="text-secondary">Move the work item to</span>
                {stateSelect(
                  rule.target_state_id,
                  (stateId) => patchRule(rule, { target_state_id: stateId }),
                  !isAdmin || saving,
                  `rule-state-${rule.id}`
                )}
              </label>
              <div className="space-y-1 text-13">
                <span className="block text-secondary">Assign the work item to</span>
                <div className="flex items-center gap-2">
                  <MemberSelect
                    projectId={projectId}
                    value={rule.assignee_id}
                    disabled={!isAdmin || saving}
                    onChange={(id: string) => patchRule(rule, { assignee_id: id || null })}
                    placeholder="No assignment"
                    variant="select-ghost-md"
                    tooltip={{ heading: "Assignee" }}
                  />
                  {rule.assignee_id && isAdmin && (
                    <Button
                      size="sm"
                      stretch="auto"
                      variant="ghost"
                      disabled={saving}
                      onClick={() => patchRule(rule, { assignee_id: null })}
                      label="Clear"
                    />
                  )}
                </div>
              </div>
            </div>
            <label className="mt-3 flex items-center gap-2 text-12 text-secondary">
              <input
                type="checkbox"
                checked={rule.require_all_merged}
                disabled={!isAdmin || saving}
                onChange={(event) => patchRule(rule, { require_all_merged: event.target.checked })}
              />
              Only act once every linked pull request has merged
            </label>
          </li>
        ))}

        {draftOpen && (
          <li className="rounded-lg border border-dashed border-subtle p-4">
            <p className="text-13 font-medium">New rule</p>
            <div className="mt-2 grid gap-3 md:grid-cols-2">
              <label className="block space-y-1 text-13">
                <span className="text-secondary">Move the work item to</span>
                {stateSelect(null, (stateId) => createRule({ target_state_id: stateId }), saving, "draft-state")}
              </label>
              <div className="space-y-1 text-13">
                <span className="block text-secondary">Assign the work item to</span>
                <MemberSelect
                  projectId={projectId}
                  value={null}
                  disabled={saving}
                  onChange={(id: string) => id && createRule({ assignee_id: id })}
                  placeholder="No assignment"
                  variant="select-ghost-md"
                  tooltip={{ heading: "Assignee" }}
                />
              </div>
            </div>
            <p className="mt-2 text-12 text-secondary">
              Pick a state or an assignee to save the rule — branch and options can be tuned afterwards.
            </p>
            <div className="mt-2">
              <Button size="sm" stretch="auto" variant="ghost" onClick={() => setDraftOpen(false)} label="Cancel" />
            </div>
          </li>
        )}
      </ul>

      {!isAdmin && data && rules.length > 0 && (
        <p className="mt-3 text-12 text-secondary">Only project admins can change automation.</p>
      )}
      {error && (
        <p role="alert" className="mt-2 text-12">
          {githubError(error)}
        </p>
      )}
      {message && (
        <p role="status" className="mt-2 text-12">
          {message}
        </p>
      )}
    </section>
  );
});

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
        <DevelopmentAutomationCard workspaceSlug={workspaceSlug} projectId={projectId} />
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
              Connect GitHub in integration settings — a click creates a private GitHub App for github.com or your
              Enterprise server — then map a repository to this project. Pull requests that mention this project’s work
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

const TIMELINE_ICONS = {
  commit: GitCommit,
  pr_opened: CircleDot,
  pr_merged: GitMerge,
  pr_closed: GitPullRequest,
  review: MessageSquare,
} as const;

const TIMELINE_LABELS = {
  commit: "Commit",
  pr_opened: "Pull request opened",
  pr_merged: "Merged to the main branch",
  pr_closed: "Pull request closed",
  review: "Review",
} as const;

/** Review events carry their verdict in the title/detail text: "Review approved by octocat", "changes requested". */
function reviewIconStyle(event: GithubTimelineEvent): { Icon: typeof ThumbsUp; tone: string } {
  const text = `${event.title} ${event.detail}`.toLowerCase();
  if (text.includes("changes requested") || text.includes("changes_requested"))
    return { Icon: ThumbsDown, tone: "text-red-500" };
  if (text.includes("approved")) return { Icon: ThumbsUp, tone: "text-purple-500" };
  return { Icon: MessageSquare, tone: "text-secondary" };
}

function DevelopmentTimelineDialog({
  open,
  onOpenChange,
  events,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  events: GithubTimelineEvent[];
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent size="md">
        <DialogMain>
          <DialogHeader>
            <DialogHeading>
              <DialogTitle>Development timeline</DialogTitle>
            </DialogHeading>
          </DialogHeader>
          <DialogBody render={<div className="space-y-0" />}>
            {events.length === 0 && (
              <p className="py-6 text-center text-13 text-secondary">
                No development activity yet. Mention this work item&rsquo;s key in commits or pull requests on GitHub
                and the story builds itself here.
              </p>
            )}
            {events.length > 0 && (
              <ol className="relative ml-2 space-y-0 border-l border-subtle">
                {events.map((event) => {
                  const review = event.kind === "review" ? reviewIconStyle(event) : undefined;
                  const Icon = review?.Icon ?? TIMELINE_ICONS[event.kind];
                  return (
                    <li key={`${event.kind}-${event.url}`} className="relative py-3 pl-6">
                      <span className="absolute top-4 -left-[13px] flex size-6 items-center justify-center rounded-full border border-subtle bg-surface-1">
                        <Icon
                          className={
                            review
                              ? `size-3 ${review.tone}`
                              : event.kind === "pr_merged"
                                ? "text-purple-500 size-3"
                                : event.kind === "pr_closed"
                                  ? "text-red-500 size-3"
                                  : "size-3 text-secondary"
                          }
                          aria-hidden
                        />
                      </span>
                      <a
                        href={event.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-13 font-medium hover:underline"
                      >
                        {event.title}
                        <ExternalLink className="ml-1 inline size-3 text-tertiary" aria-hidden />
                      </a>
                      <MetaRow>
                        {TIMELINE_LABELS[event.kind]}
                        {event.detail ? ` · ${event.detail}` : ""}
                        {event.repository ? ` · ${event.repository}` : ""}
                        {(event.author_user_id || event.author) && (
                          <>
                            {" · "}
                            <AuthorBadge userId={event.author_user_id} fallback={event.author} />
                          </>
                        )}
                        {event.at ? ` · ${new Date(event.at).toLocaleString()}` : ""}
                      </MetaRow>
                    </li>
                  );
                })}
              </ol>
            )}
            <p className="pt-2 text-12 text-tertiary">
              Oldest first — from the first commit to the merge that landed the work.
            </p>
          </DialogBody>
        </DialogMain>
      </DialogContent>
    </Dialog>
  );
}

export function IssueDevelopment({
  workspaceSlug,
  projectId,
  issueId,
}: {
  workspaceSlug: string;
  projectId: string;
  issueId: string;
  disabled: boolean;
}) {
  const [timelineOpen, setTimelineOpen] = useState(false);
  const { data, error, isLoading } = useSWR(
    ["github-issue-development", workspaceSlug, projectId, issueId],
    () => service.issueDevelopment(workspaceSlug, projectId, issueId),
    // While the mention search runs, poll so discovered PRs and commits appear.
    { refreshInterval: (latest) => (latest?.mention_search.running ? 8000 : 0) }
  );
  const searching = data?.mention_search.running === true;
  const pullRequests = data?.pull_requests ?? [];
  const commits = data?.commits ?? [];
  const timeline = data?.timeline ?? [];
  const total = pullRequests.length + commits.length;
  // Project guests do not have access to private development metadata.
  if ((error as { response?: { status?: number } })?.response?.status === 403) return null;
  return (
    <>
      <SidebarSectionCard
        label="Development"
        appendElement={
          timeline.length > 0 ? (
            <Button
              size="sm"
              stretch="auto"
              variant="ghost"
              onClick={() => setTimelineOpen(true)}
              icon={<History className="size-3.5" aria-hidden />}
              label="Timeline"
            />
          ) : (
            <span className="text-12 text-tertiary">{total}</span>
          )
        }
      >
        {searching && (
          <p role="status" className="flex items-center gap-2 py-1 text-12 text-secondary">
            <CircleDot className="size-3 animate-pulse" aria-hidden />
            Searching GitHub for commits and pull requests that mention this work item…
          </p>
        )}
        {!searching && data?.mention_search.error ? (
          <p role="alert" className="py-1 text-12">
            GitHub mention search failed: {data.mention_search.error}
          </p>
        ) : null}
        {error && (
          <p role="alert" className="py-1 text-12">
            {githubError(error)}
          </p>
        )}
        {isLoading && (
          <p role="status" className="py-1 text-12 text-secondary">
            Loading development activity…
          </p>
        )}
        {pullRequests.length > 0 && (
          <div className="py-1">
            <p className="text-11 font-medium tracking-wide text-tertiary uppercase">Pull requests</p>
            <ul>
              {pullRequests.map((pr) => (
                <PullRequestRow key={pr.id} pullRequest={pr} />
              ))}
            </ul>
          </div>
        )}
        {commits.length > 0 && (
          <div className="py-1">
            <p className="text-11 font-medium tracking-wide text-tertiary uppercase">Commits</p>
            <ul>
              {commits.map((commit) => (
                <CommitRow key={commit.id} commit={commit} />
              ))}
            </ul>
          </div>
        )}
        {data && total === 0 && !searching && !isLoading && (
          <p className="py-1 text-12 text-secondary">
            Nothing linked from GitHub yet. Mention this work item&rsquo;s key on GitHub — in a commit message, pull
            request title, description or branch — and it shows up here automatically.
          </p>
        )}
      </SidebarSectionCard>
      <DevelopmentTimelineDialog open={timelineOpen} onOpenChange={setTimelineOpen} events={timeline} />
    </>
  );
}
