/** Copyright (c) 2023-present Plane Software, Inc. and contributors. SPDX-License-Identifier: AGPL-3.0-only */
import { useState } from "react";
import { observer } from "mobx-react";
import useSWR from "swr";
import { Slack, RefreshCw } from "lucide-react";
import { Button } from "@makeplane/propel/components/button";
import { useProject } from "@/hooks/store/use-project";
import { slackDeliveryService as service, slackError } from "@/services/integrations/slack-delivery.service";

export const SlackDeliverySettings = observer(function SlackDeliverySettings({
  workspaceSlug,
}: {
  workspaceSlug: string;
}) {
  const { joinedProjectIds, getProjectById } = useProject();
  const { data, error, mutate, isLoading } = useSWR(
    ["slack-delivery-status", workspaceSlug],
    () => service.status(workspaceSlug),
    {
      refreshInterval: (value) => (value?.mappings.some((mapping) => mapping.sync_status === "pending") ? 10000 : 0),
    }
  );
  const [connectionId, setConnectionId] = useState("");
  const [channelId, setChannelId] = useState("");
  const [projectId, setProjectId] = useState("");
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState("");
  const {
    data: channels,
    error: channelsError,
    isLoading: loadingChannels,
  } = useSWR(connectionId ? ["slack-delivery-channels", workspaceSlug, connectionId] : null, () =>
    service.channels(workspaceSlug, connectionId)
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
      setMessage(slackError(cause));
    } finally {
      setPending(false);
    }
  };
  const selectClass = "w-full rounded-md border border-subtle bg-surface-1 px-3 py-2 text-13";
  return (
    <section aria-labelledby="slack-delivery-heading" className="space-y-5 border-b border-subtle py-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex gap-3">
          <Slack className="mt-0.5 size-6 shrink-0" aria-hidden />
          <div>
            <h4 id="slack-delivery-heading" className="text-16 font-semibold">
              Slack
            </h4>
            <p className="mt-1 max-w-xl text-13 text-secondary">
              Connect Slack channels to track conversations alongside your work. The app reads history only — it never
              joins channels on its own and never posts messages.
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
            label="Connect Slack"
          />
        )}
      </div>
      {isLoading && (
        <p role="status" className="text-13 text-secondary">
          Loading Slack connections…
        </p>
      )}
      {error && (
        <div role="alert" className="text-13">
          <p>{slackError(error)}</p>
          <Button size="sm" stretch="auto" variant="secondary" onClick={() => void mutate()} label="Try again" />
        </div>
      )}
      {data && !data.configured && (
        <div className="rounded-lg border border-subtle bg-surface-2 p-4 text-13">
          <p className="font-medium">Slack setup is not complete</p>
          <p className="mt-1 text-secondary">
            An instance administrator needs to configure a Slack app before you can connect your workspace. No channels
            are read automatically.
          </p>
          <details className="mt-3">
            <summary className="cursor-pointer font-medium">Setup details for your administrator</summary>
            <p className="mt-2 text-secondary">
              Create a Slack app with bot token scopes {data.scopes.join(", ")}. Subscribe to the events{" "}
              {data.event_subscriptions.join(", ")} and invite the app to every channel you plan to connect; the app
              never joins channels by itself and never writes to them.
            </p>
            {data.missing_settings.length > 0 && (
              <p className="mt-2 break-words">Missing settings: {data.missing_settings.join(", ")}</p>
            )}
            {data.configuration_error && <p className="mt-2">{data.configuration_error}</p>}
            <dl className="mt-2 space-y-2 break-all">
              {[
                ["Redirect URL", data.callback_url],
                ["Events URL", data.events_url],
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
            <p className="text-14 font-medium">{connection.team_name}</p>
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
                    setChannelId("");
                  }
                }, "Slack disconnected. Linked conversation history is still available.")
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
                channel_id: channelId,
                project_id: projectId,
              });
              setChannelId("");
            }, "Channel connected. Recent messages are syncing.");
          }}
        >
          <h5 className="text-14 font-medium">Connect a Slack channel to a project</h5>
          <div className="grid gap-3 md:grid-cols-3">
            <label className="space-y-1 text-13">
              <span>Slack workspace</span>
              <select
                className={selectClass}
                value={connectionId}
                required
                disabled={pending}
                onChange={(event) => {
                  setConnectionId(event.target.value);
                  setChannelId("");
                }}
              >
                <option value="">Choose a workspace</option>
                {data.connections
                  .filter((item) => item.active)
                  .map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.team_name}
                    </option>
                  ))}
              </select>
            </label>
            <label className="space-y-1 text-13">
              <span>Channel</span>
              <select
                className={selectClass}
                value={channelId}
                required
                disabled={pending || !channels}
                onChange={(event) => setChannelId(event.target.value)}
              >
                <option value="">{loadingChannels ? "Loading channels…" : "Choose a channel"}</option>
                {channels?.map((channel) => (
                  <option key={channel.id} value={channel.id} disabled={!channel.member}>
                    #{channel.name}
                    {channel.private ? " (private)" : ""}
                    {channel.member ? "" : " — app not invited"}
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
          {channelsError && (
            <p role="alert" className="text-13">
              {slackError(channelsError)}
            </p>
          )}
          {channels?.length === 0 && (
            <p className="text-13 text-secondary">
              No channels are available. Invite the Slack app to a channel, then reload this page.
            </p>
          )}
          <p className="text-12 text-secondary">
            Only channels the app has been invited to are available. Each channel connects to one project at a time.
          </p>
          <Button
            size="sm"
            stretch="auto"
            variant="primary"
            type="submit"
            disabled={pending || !connectionId || !channelId || !projectId}
            label="Connect channel"
          />
        </form>
      )}
      {data && data.mappings.length > 0 && (
        <div className="space-y-2">
          <h5 className="text-14 font-medium">Connected channels</h5>
          {data.mappings.map((mapping) => (
            <div
              key={mapping.id}
              className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-subtle p-3"
            >
              <div className="min-w-0">
                <p className="text-13 font-medium break-words">
                  #{mapping.channel} → {getProjectById(mapping.project_id)?.name ?? "Project"}
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
                      "Channel disconnected. History retained."
                    )
                  }
                  label="Disconnect channel"
                />
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
});
