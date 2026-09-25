/** Copyright (c) 2023-present Plane Software, Inc. and contributors. SPDX-License-Identifier: AGPL-3.0-only */
import { useState } from "react";
import { Link } from "react-router";
import useSWR from "swr";
import { ExternalLink, MessageSquare, Slack } from "lucide-react";
import { Button } from "@makeplane/propel/components/button";
import { slackDeliveryService as service, slackError } from "@/services/integrations/slack-delivery.service";
import type { SlackLinkedMessage } from "@/services/integrations/slack-delivery.service";

function MessageRow({
  message,
  onUnlink,
  pending,
}: {
  message: SlackLinkedMessage;
  onUnlink?: () => void;
  pending?: boolean;
}) {
  return (
    <li className="flex items-start gap-3 border-b border-subtle py-3 last:border-0">
      <MessageSquare className="mt-0.5 size-4 shrink-0 text-secondary" aria-hidden />
      <div className="min-w-0 flex-1">
        {message.url ? (
          <a
            href={message.url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-13 font-medium break-words hover:underline"
          >
            #{message.channel} — {message.user || "unknown member"}
            <ExternalLink className="ml-1 inline size-3" aria-hidden />
          </a>
        ) : (
          <p className="text-13 font-medium break-words">
            #{message.channel} — {message.user || "unknown member"}
          </p>
        )}
        <p className="mt-1 whitespace-pre-wrap break-words text-12 text-secondary">{message.text}</p>
        <p className="mt-1 text-12 break-words text-secondary">
          {message.posted_at ? new Date(message.posted_at).toLocaleString() : ""}
          {message.thread_ts ? " · thread reply" : ""}
          {!message.connected ? " · Disconnected" : ""}
        </p>
      </div>
      {onUnlink && (
        <Button
          size="sm"
          stretch="auto"
          variant="secondary"
          disabled={pending}
          aria-label="Unlink Slack message"
          onClick={onUnlink}
          label="Unlink"
        />
      )}
    </li>
  );
}

export function ProjectConversations({ workspaceSlug, projectId }: { workspaceSlug: string; projectId: string }) {
  const { data, error, isLoading, mutate } = useSWR(
    ["slack-project-conversations", workspaceSlug, projectId],
    () => service.conversations(workspaceSlug, projectId),
    { refreshInterval: 30000 }
  );
  return (
    <section className="h-full overflow-y-auto px-5 py-6 md:px-8">
      <div className="mx-auto max-w-5xl space-y-6">
        <header className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h1 className="text-24 font-semibold">Conversations</h1>
            <p className="mt-1 text-13 text-secondary">
              Slack channel messages connected to this project. Messages that mention a work item key appear on that
              work item.
            </p>
          </div>
          <Button size="sm" stretch="auto" variant="secondary" onClick={() => void mutate()} label="Refresh" />
        </header>
        {isLoading && (
          <p role="status" className="text-13 text-secondary">
            Loading conversation activity…
          </p>
        )}
        {error && (
          <div role="alert" className="rounded-lg border border-subtle p-4 text-13">
            {slackError(error)}{" "}
            <Button size="sm" stretch="auto" variant="secondary" onClick={() => void mutate()} label="Try again" />
          </div>
        )}
        {data && data.channels.length === 0 && (
          <div className="rounded-xl border border-subtle p-8 text-center">
            <Slack className="mx-auto size-8 text-secondary" aria-hidden />
            <h2 className="mt-4 text-18 font-medium">Bring your conversations into the picture</h2>
            <p className="mx-auto mt-2 max-w-md text-13 text-secondary">
              A workspace administrator can connect a Slack channel the app has been invited to. Messages that mention
              this project’s work item keys will then appear on the matching work items.
            </p>
            <Link
              to={`/${workspaceSlug}/settings/integrations/`}
              className="mt-5 inline-block text-13 font-medium text-accent-primary hover:underline"
            >
              Open integration settings
            </Link>
          </div>
        )}
        {data && data.channels.length > 0 && (
          <>
            <div className="flex flex-wrap gap-2">
              {data.channels.map((channel) => (
                <span key={channel.id} className="rounded-md border border-subtle px-3 py-1.5 text-12">
                  #{channel.channel}
                  {channel.private ? " (private)" : ""}
                  {channel.active
                    ? channel.sync_status === "pending"
                      ? " · Sync queued"
                      : channel.sync_status === "failed"
                        ? " · Sync needs attention"
                        : ""
                    : " · Disconnected"}
                </span>
              ))}
            </div>
            <section className="rounded-xl border border-subtle p-5" aria-labelledby="conversations-messages">
              <h2 id="conversations-messages" className="text-16 font-semibold">
                Messages <span className="font-normal text-12 text-secondary">{data.messages.length}</span>
              </h2>
              {data.messages.length ? (
                <ul className="mt-2">
                  {data.messages.map((message) => (
                    <MessageRow key={message.id} message={message} />
                  ))}
                </ul>
              ) : (
                <p className="py-6 text-13 text-secondary">
                  No messages have synced yet. New activity will appear here after Slack sends an event.
                </p>
              )}
            </section>
            <p className="text-12 text-secondary">
              Showing recent activity, up to 200 messages. Initial sync includes the latest 100 messages per channel.
              This integration reads channel history only and never posts.
            </p>
          </>
        )}
      </div>
    </section>
  );
}

export function IssueConversations({
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
    ["slack-issue-conversations", workspaceSlug, projectId, issueId],
    () => service.issueMessages(workspaceSlug, projectId, issueId)
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
      setMessage(slackError(cause));
    } finally {
      setPending(false);
    }
  };
  // Project guests do not have access to private conversation metadata.
  if ((error as { response?: { status?: number } })?.response?.status === 403) return null;
  return (
    <details className="rounded-lg border border-subtle p-3">
      <summary className="cursor-pointer text-13 font-medium">
        Conversations{data?.length ? ` (${data.length})` : ""}
      </summary>
      {isLoading && (
        <p role="status" className="mt-3 text-12 text-secondary">
          Loading linked Slack messages…
        </p>
      )}
      {error && (
        <p role="alert" className="mt-3 text-12">
          {slackError(error)}
        </p>
      )}
      {data && (
        <ul>
          {data.map((item) => (
            <MessageRow
              key={item.id}
              message={item}
              pending={pending}
              onUnlink={disabled ? undefined : () => void unlink(item.id)}
            />
          ))}
        </ul>
      )}
      {data?.length === 0 && (
        <p className="mt-3 text-12 text-secondary">
          No linked Slack messages. Post this work item’s key in a connected channel, or link one by permalink below.
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
              setMessage(slackError(cause));
            } finally {
              setPending(false);
            }
          }}
        >
          <label className="min-w-0 flex-1 space-y-1 text-12">
            <span>Slack message permalink</span>
            <input
              type="url"
              required
              value={url}
              disabled={pending}
              onChange={(event) => setUrl(event.target.value)}
              placeholder="https://your-team.slack.com/archives/C0123456789/p1727251203000001"
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
