/** Copyright (c) 2023-present Plane Software, Inc. and contributors. SPDX-License-Identifier: AGPL-3.0-only */
import { useState } from "react";
import useSWR from "swr";
import {
  CheckCircle2,
  ExternalLink,
  GitCommit,
  GitMerge,
  GitPullRequest,
  Link2,
  MessageSquare,
  RefreshCw,
  XCircle,
} from "lucide-react";
import { calculateTimeAgo } from "@plane/utils";
import { Loader } from "@plane/blocks/skeleton";
import { Button } from "@makeplane/propel/components/button";
import {
  linkedActivityService,
  type TLinkedActivityEvent,
  type TLinkedActivitySource,
} from "@/services/integrations/linked-activity.service";

const PAGE_SIZE = 50;

type SourceStyle = { dot: string; glyph: string };

const SOURCE_STYLES: Record<TLinkedActivitySource, SourceStyle> = {
  github: { dot: "bg-violet-500", glyph: "text-violet-500" },
  asana: { dot: "bg-rose-500", glyph: "text-rose-500" },
  slack: { dot: "bg-emerald-500", glyph: "text-emerald-500" },
};

const FAILING_CONCLUSIONS = ["failure", "timed_out", "cancelled", "startup_failure"];

function eventIcon(event: TLinkedActivityEvent): { Icon: typeof MessageSquare; tone: string } {
  const meta = (event.meta ?? {}) as { failed?: boolean; conclusion?: string; review_state?: string };
  switch (event.type) {
    case "pr_merged":
      return { Icon: GitMerge, tone: "text-violet-500" };
    case "pr_opened":
    case "pr_closed":
      return { Icon: GitPullRequest, tone: event.type === "pr_closed" ? "text-red-500" : "text-secondary" };
    case "commit":
      return { Icon: GitCommit, tone: "text-secondary" };
    case "check_completed":
      return meta.failed || FAILING_CONCLUSIONS.includes(String(meta.conclusion))
        ? { Icon: XCircle, tone: "text-red-500" }
        : { Icon: CheckCircle2, tone: "text-green-500" };
    case "review":
      return String(meta.review_state).toLowerCase() === "changes_requested"
        ? { Icon: XCircle, tone: "text-red-500" }
        : String(meta.review_state).toLowerCase() === "approved"
          ? { Icon: CheckCircle2, tone: "text-violet-500" }
          : { Icon: MessageSquare, tone: "text-secondary" };
    case "sync_ok":
      return { Icon: RefreshCw, tone: "text-secondary" };
    case "task_linked":
    case "comment_synced":
    case "thread_linked":
      return { Icon: Link2, tone: "text-secondary" };
    case "slack_message":
      return { Icon: MessageSquare, tone: "text-secondary" };
    default:
      return { Icon: Link2, tone: "text-secondary" };
  }
}

function TimelineRow({ event }: { event: TLinkedActivityEvent }) {
  const { Icon, tone } = eventIcon(event);
  const source = SOURCE_STYLES[event.source] ?? SOURCE_STYLES.github;
  const isError = event.type === "sync_error";
  const timestamp = event.timestamp ?? "";
  const relative = timestamp ? calculateTimeAgo(timestamp) : "";

  const title = (
    <>
      <span className={`text-13 font-medium ${isError ? "text-red-500" : ""}`}>{event.title}</span>
      {event.url && (
        <ExternalLink
          className="size-3 shrink-0 text-tertiary opacity-0 transition-opacity group-hover:opacity-100"
          aria-hidden
        />
      )}
    </>
  );

  return (
    <li
      className={`group relative rounded-md py-2.5 pl-8 pr-2 ${
        isError ? "bg-red-500/5" : "hover:bg-custom-background-80"
      }`}
    >
      <span
        className={`absolute top-3.5 -left-[13px] flex size-6 items-center justify-center rounded-full border border-subtle bg-surface-1 ${tone}`}
      >
        <Icon className="size-3" aria-hidden />
      </span>
      {event.url ? (
        <a
          href={event.url}
          target="_blank"
          rel="noopener noreferrer"
          className="flex min-w-0 flex-wrap items-center gap-x-1.5"
        >
          {title}
        </a>
      ) : (
        <span className="flex min-w-0 flex-wrap items-center gap-x-1.5">{title}</span>
      )}
      <p className="mt-0.5 flex flex-wrap items-center gap-x-1.5 gap-y-1 text-12 text-tertiary" title={timestamp}>
        <span className={`size-1.5 shrink-0 rounded-full ${source.dot}`} aria-hidden />
        {event.source}
        {relative && <span>· {relative}</span>}
        {event.actor && (
          <span className="inline-flex max-w-40 items-center truncate rounded-full bg-surface-1 px-2 py-0.5 text-11 text-secondary">
            {event.actor}
          </span>
        )}
      </p>
    </li>
  );
}

function TimelineSkeleton() {
  return (
    <div role="status" aria-label="Loading linked activity">
      <Loader className="space-y-6 pt-2">
        <div className="flex items-start gap-3">
          <Loader.Item className="shrink-0 rounded-full" height="24px" width="24px" />
          <div className="w-full space-y-2">
            <Loader.Item height="10px" width="55%" />
            <Loader.Item height="8px" width="30%" />
          </div>
        </div>
        <div className="flex items-start gap-3">
          <Loader.Item className="shrink-0 rounded-full" height="24px" width="24px" />
          <div className="w-full space-y-2">
            <Loader.Item height="10px" width="70%" />
            <Loader.Item height="8px" width="25%" />
          </div>
        </div>
        <div className="flex items-start gap-3">
          <Loader.Item className="shrink-0 rounded-full" height="24px" width="24px" />
          <div className="w-full space-y-2">
            <Loader.Item height="10px" width="60%" />
            <Loader.Item height="8px" width="35%" />
          </div>
        </div>
      </Loader>
    </div>
  );
}

export function IssueLinkedActivity({
  workspaceSlug,
  projectId,
  issueId,
}: {
  workspaceSlug: string;
  projectId: string;
  issueId: string;
}) {
  const [pageSize, setPageSize] = useState(PAGE_SIZE);
  const { data, error, isLoading, isValidating } = useSWR(
    ["linked-activity", workspaceSlug, projectId, issueId, pageSize],
    () => linkedActivityService.getLinkedActivity(workspaceSlug, projectId, issueId, { limit: pageSize, offset: 0 }),
    { revalidateOnFocus: false }
  );

  // Project guests do not have access to integration metadata.
  if ((error as { response?: { status?: number } })?.response?.status === 403) return null;

  const events = data?.results ?? [];
  const count = data?.count ?? 0;
  const hasMore = data != null && data.offset + data.limit < data.count;

  return (
    <section aria-label="Linked activity">
      <div className="flex items-center gap-2">
        <h2 className="text-h5-medium text-primary">Linked activity</h2>
        {count > 0 && (
          <span className="rounded-full bg-surface-1 px-2 py-0.5 text-11 text-secondary">{count}</span>
        )}
      </div>

      {isLoading && <TimelineSkeleton />}

      {error && !isLoading && (
        <p role="alert" className="mt-2 text-12">
          Linked activity could not be loaded. Please try again.
        </p>
      )}

      {!isLoading && !error && events.length === 0 && (
        <p className="mt-2 text-12 text-secondary">
          No linked activity yet — connect GitHub, Asana or Slack
        </p>
      )}

      {!isLoading && events.length > 0 && (
        <>
          <ol className="relative ml-2 mt-2 border-l-2 border-subtle">
            {events.map((event) => (
              <TimelineRow key={event.id} event={event} />
            ))}
          </ol>
          {hasMore && (
            <div className="mt-2 pl-8">
              <Button
                size="sm"
                stretch="auto"
                variant="secondary"
                loading={isValidating}
                onClick={() => setPageSize((current) => current + PAGE_SIZE)}
                label="Load more"
              />
            </div>
          )}
        </>
      )}
    </section>
  );
}
