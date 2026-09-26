/** Copyright (c) 2023-present Plane Software, Inc. and contributors. SPDX-License-Identifier: AGPL-3.0-only */
import { useState } from "react";
import { observer } from "mobx-react";
import useSWR from "swr";
import { Slack, RefreshCw } from "lucide-react";
import { Button } from "@makeplane/propel/components/button";
import { IntegrationDisclosure } from "@/components/integrations/disclosure";
import { useProject } from "@/hooks/store/use-project";
import {
  slackDeliveryService as service,
  slackError,
  type SlackMapping,
  type SlackNotifyEvents,
} from "@/services/integrations/slack-delivery.service";

const SLACK_COMMANDS: [string, string][] = [
  ["/plane create <title>", "Create a work item in the channel's connected project"],
  ["/plane view <ref>", "Show a work item"],
  ["/plane assign <ref> <user>", "Assign a member"],
  ["/plane label <ref> <labels>", "Set labels"],
  ["/plane state <ref> <state>", "Move to a state"],
  ["/plane comment <ref> <text>", "Add a comment"],
  ["/plane close <ref>", "Close a work item"],
  ["/plane list", "List recent work items"],
  ["/plane help", "Show every command"],
];

function statusBadge(label: string) {
  return (
    <span className="rounded-full border border-subtle px-2 py-px text-10 font-normal text-secondary">{label}</span>
  );
}

const NOTIFY_FIELDS: { key: keyof SlackNotifyEvents; label: string; short: string }[] = [
  { key: "notify_created", label: "Work items created", short: "created" },
  { key: "notify_state_changed", label: "State changes", short: "state changes" },
  { key: "notify_assigned", label: "Assignments", short: "assignments" },
  { key: "notify_commented", label: "Comments", short: "comments" },
];

function notifyEvents(mapping: SlackMapping): SlackNotifyEvents {
  return {
    notify_created: mapping.notify_created ?? false,
    notify_state_changed: mapping.notify_state_changed ?? false,
    notify_assigned: mapping.notify_assigned ?? false,
    notify_commented: mapping.notify_commented ?? false,
  };
}

function NotifyEditor({
  draft,
  saving,
  message,
  onChange,
  onSave,
}: {
  draft: SlackNotifyEvents;
  saving: boolean;
  message: string;
  onChange: (key: keyof SlackNotifyEvents, value: boolean) => void;
  onSave: () => void;
}) {
  const posts = NOTIFY_FIELDS.filter((field) => draft[field.key]).map((field) => field.short);
  return (
    <div className="w-full space-y-2 rounded-md border border-subtle bg-surface-1 p-3 text-13">
      <div className="grid gap-2 md:grid-cols-2">
        {NOTIFY_FIELDS.map((field) => (
          <label key={field.key} className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={draft[field.key]}
              disabled={saving}
              onChange={(event) => onChange(field.key, event.target.checked)}
            />
            <span>{field.label}</span>
          </label>
        ))}
      </div>
      <Button
        size="sm"
        stretch="auto"
        variant="primary"
        type="button"
        disabled={saving}
        onClick={onSave}
        label="Save notifications"
      />
      <p className="text-12 text-secondary">
        {posts.length === 0 ? "Notifications are off for this channel." : `Posts: ${posts.join(", ")}`}
      </p>
      {message && (
        <p role="status" className="text-13">
          {message}
        </p>
      )}
    </div>
  );
}

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
  const {
    data: setup,
    error: setupError,
    mutate: mutateSetup,
  } = useSWR(["slack-delivery-setup", workspaceSlug], () => service.setupStatus(workspaceSlug));
  const setupForbidden = (setupError as { response?: { status?: number } } | undefined)?.response?.status === 403;
  const [connectionId, setConnectionId] = useState("");
  const [channelId, setChannelId] = useState("");
  const [projectId, setProjectId] = useState("");
  const [clientId, setClientId] = useState("");
  const [clientSecret, setClientSecret] = useState("");
  const [signingSecret, setSigningSecret] = useState("");
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState("");
  const [notifyOpenId, setNotifyOpenId] = useState<string | null>(null);
  const [notifyDraft, setNotifyDraft] = useState<Record<string, SlackNotifyEvents>>({});
  const [notifySavingId, setNotifySavingId] = useState<string | null>(null);
  const [notifyMessages, setNotifyMessages] = useState<Record<string, string>>({});
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
  const saveNotify = async (mapping: SlackMapping) => {
    const draft = notifyDraft[mapping.id] ?? notifyEvents(mapping);
    setNotifySavingId(mapping.id);
    setNotifyMessages((current) => ({ ...current, [mapping.id]: "" }));
    try {
      await mutate(
        (current) =>
          current && {
            ...current,
            mappings: current.mappings.map((item) => (item.id === mapping.id ? { ...item, ...draft } : item)),
          },
        { revalidate: false }
      );
      await service.saveMappingNotify(workspaceSlug, mapping.id, draft);
      setNotifyMessages((current) => ({ ...current, [mapping.id]: "Notification settings saved." }));
    } catch (cause) {
      await mutate();
      setNotifyMessages((current) => ({ ...current, [mapping.id]: slackError(cause) }));
    } finally {
      setNotifySavingId(null);
    }
  };
  const saveCredentials = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    void (async () => {
      setPending(true);
      setMessage("");
      try {
        await service.saveSetup(workspaceSlug, {
          client_id: clientId.trim(),
          client_secret: clientSecret.trim(),
          signing_secret: signingSecret.trim(),
        });
        setClientSecret("");
        setSigningSecret("");
        await Promise.all([mutateSetup(), mutate()]);
        setMessage("App connected to this instance.");
      } catch (cause) {
        setMessage(slackError(cause));
      } finally {
        setPending(false);
      }
    })();
  };
  const fieldClass = "w-full rounded-md border border-subtle bg-surface-1 px-3 py-2 text-13";
  const hasActiveConnection = data?.connections.some((connection) => connection.active);
  const status =
    data === undefined
      ? undefined
      : hasActiveConnection
        ? statusBadge("Connected")
        : !data.configured
          ? statusBadge("Setup needed")
          : undefined;
  const reference = setup ?? data;
  return (
    <IntegrationDisclosure
      id="slack"
      icon={<Slack className="size-6" aria-hidden />}
      title="Slack"
      description="Connect channels alongside work, create and manage work items from Slack, and preview Plane links in conversation."
      status={status}
      actions={
        data?.configured && (
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
        )
      }
    >
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
        <div className="space-y-4 rounded-lg border border-subtle bg-surface-2 p-4 text-13">
          {setupForbidden ? (
            <div>
              <p className="font-medium">Slack setup is not complete</p>
              <p className="mt-1 text-secondary">
                A workspace administrator needs to finish setting up the Slack app for this instance. No channels are
                read automatically.
              </p>
            </div>
          ) : (
            <>
              <div className="space-y-2">
                <p className="font-medium">1. Create the Slack app</p>
                <p className="text-secondary">
                  Slack opens with everything pre-configured — name, scopes, commands, and event URLs. Create the app in
                  your workspace, then copy the three values from its Basic Information → App Credentials.
                </p>
                <Button
                  size="sm"
                  stretch="auto"
                  variant="primary"
                  disabled={!setup?.setup_url}
                  onClick={() => {
                    const url = setup?.setup_url;
                    if (url) window.open(url, "_blank", "noopener");
                  }}
                  label="Create Slack app"
                />
                {setup && !setup.setup_url && (
                  <p className="text-secondary">
                    {setup.configuration_error ?? "Slack app creation is not available for this instance."}
                  </p>
                )}
                {setupError && !setupForbidden && <p role="alert">{slackError(setupError)}</p>}
              </div>
              {setup && (
                <form className="space-y-3" onSubmit={saveCredentials}>
                  <div>
                    <p className="font-medium">2. Paste the app credentials</p>
                    {setup.app.configured ? (
                      <p className="mt-1 text-secondary">
                        Credentials are already saved (Client ID {setup.app.client_id_masked}
                        {setup.app.updated_at ? `, updated ${new Date(setup.app.updated_at).toLocaleString()}` : ""}
                        ). Saving again replaces them.
                      </p>
                    ) : (
                      <p className="mt-1 text-secondary">
                        Paste the Client ID, Client Secret, and Signing Secret from the app you just created.
                      </p>
                    )}
                  </div>
                  <div className="grid gap-3 md:grid-cols-3">
                    <label className="space-y-1">
                      <span>Client ID</span>
                      <input
                        className={fieldClass}
                        value={clientId}
                        required
                        disabled={pending}
                        autoComplete="off"
                        onChange={(event) => setClientId(event.target.value)}
                      />
                    </label>
                    <label className="space-y-1">
                      <span>Client Secret</span>
                      <input
                        type="password"
                        className={fieldClass}
                        value={clientSecret}
                        required
                        disabled={pending}
                        autoComplete="new-password"
                        onChange={(event) => setClientSecret(event.target.value)}
                      />
                    </label>
                    <label className="space-y-1">
                      <span>Signing Secret</span>
                      <input
                        type="password"
                        className={fieldClass}
                        value={signingSecret}
                        required
                        disabled={pending}
                        autoComplete="new-password"
                        onChange={(event) => setSigningSecret(event.target.value)}
                      />
                    </label>
                  </div>
                  <Button
                    size="sm"
                    stretch="auto"
                    variant="primary"
                    type="submit"
                    disabled={pending}
                    label="Save credentials"
                  />
                </form>
              )}
              {setup?.app.configured && !setup.configured && (
                <div>
                  <p className="font-medium">Almost there — the instance still needs attention</p>
                  {setup.missing_settings.length > 0 && (
                    <p className="mt-1 break-words">Missing settings: {setup.missing_settings.join(", ")}</p>
                  )}
                  {setup.configuration_error && <p className="mt-1">{setup.configuration_error}</p>}
                </div>
              )}
            </>
          )}
          <details className="mt-1">
            <summary className="cursor-pointer font-medium">Setup details for your administrator</summary>
            {reference && (
              <>
                <p className="mt-2 text-secondary">
                  Create a Slack app with bot token scopes {reference.scopes.join(", ")}. Subscribe to the events{" "}
                  {reference.event_subscriptions.join(", ")} and invite the app to every channel you plan to connect;
                  the app never joins channels by itself and never writes to them.
                </p>
                {reference.missing_settings.length > 0 && (
                  <p className="mt-2 break-words">Missing settings: {reference.missing_settings.join(", ")}</p>
                )}
                {reference.configuration_error && <p className="mt-2">{reference.configuration_error}</p>}
                <dl className="mt-2 space-y-2 break-all">
                  {[
                    ["Redirect URL", reference.callback_url],
                    ["Events URL", reference.events_url],
                    ["Commands URL", setup?.commands_url ?? null],
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
              </>
            )}
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
                className={fieldClass}
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
                className={fieldClass}
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
                className={fieldClass}
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
          {data.mappings.map((mapping) => {
            const notifyOpen = notifyOpenId === mapping.id;
            const notifySaving = notifySavingId === mapping.id;
            const notifyDraftEvents = notifyDraft[mapping.id] ?? notifyEvents(mapping);
            return (
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
                    onClick={() => {
                      if (!notifyOpen) {
                        setNotifyDraft((current) => ({ ...current, [mapping.id]: notifyEvents(mapping) }));
                        setNotifyMessages((current) => ({ ...current, [mapping.id]: "" }));
                      }
                      setNotifyOpenId(notifyOpen ? null : mapping.id);
                    }}
                    label="Notifications"
                  />
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
                {notifyOpen && (
                  <NotifyEditor
                    draft={notifyDraftEvents}
                    saving={notifySaving}
                    message={notifyMessages[mapping.id] ?? ""}
                    onChange={(key, value) =>
                      setNotifyDraft((current) => ({
                        ...current,
                        [mapping.id]: { ...(current[mapping.id] ?? notifyEvents(mapping)), [key]: value },
                      }))
                    }
                    onSave={() => void saveNotify(mapping)}
                  />
                )}
              </div>
            );
          })}
        </div>
      )}
      {data?.configured && (
        <div>
          <div className="rounded-lg border border-subtle bg-surface-2 p-4 text-13">
            <p className="font-medium">Use Slack commands</p>
            <ul className="mt-2 space-y-1">
              {SLACK_COMMANDS.map(([command, hint]) => (
                <li key={command} className="flex flex-wrap items-baseline gap-x-2">
                  <span className="font-medium break-words">{command}</span>
                  <span className="text-secondary">{hint}</span>
                </li>
              ))}
            </ul>
            <p className="mt-2 text-secondary">
              Paste any Plane work-item link in a channel the app is in and Slack shows a live preview of the ticket.
            </p>
          </div>
          <p className="mt-2 text-12 text-secondary">
            Apps created before these commands were available must be re-connected once so Slack grants the new
            permissions.
          </p>
        </div>
      )}
    </IntegrationDisclosure>
  );
});
