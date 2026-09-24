/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useContext, useEffect } from "react";
// plane imports
import { LIVE_BASE_PATH, LIVE_BASE_URL } from "@plane/constants";
// store
import { StoreContext } from "@/lib/store-context";
// local
import { applyRealtimePatch } from "@/store/issue/helpers/base-issues.store";
import type { TWorkItemRealtimeEvent } from "@/store/issue/helpers/base-issues.store";

const BASE_RECONNECT_DELAY = 1000;
const MAX_RECONNECT_DELAY = 30000;
// Auth/permission rejections are not transient — never reconnect for them.
const NO_RECONNECT_CLOSE_CODES = new Set<number>([4400, 4401, 4403]);
const WS_CONNECTING = 0;
const WS_OPEN = 1;

type TWorkitemSocketEntry = {
  socket: WebSocket | undefined;
  connectionId: string;
  refCount: number;
  reconnectAttempt: number;
  reconnectTimer: ReturnType<typeof setTimeout> | null;
  disposed: boolean;
};

// One websocket per workspace, shared by every consumer of the hook.
const socketRegistry = new Map<string, TWorkitemSocketEntry>();

const generateConnectionId = (): string =>
  typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `conn-${Math.random().toString(36).slice(2)}`;

/**
 * Builds the work-item websocket URL from `window.location`, mirroring the
 * editor's realtimeConfig construction (same LIVE_BASE_URL/LIVE_BASE_PATH
 * handling, cookies carry the auth).
 */
const buildSocketUrl = (workspaceSlug: string, connectionId: string): string => {
  const LIVE_SERVER_BASE_URL = LIVE_BASE_URL?.trim() || window.location.origin;
  const WS_LIVE_URL = new URL(LIVE_SERVER_BASE_URL);
  const isSecureEnvironment = window.location.protocol === "https:";
  WS_LIVE_URL.protocol = isSecureEnvironment ? "wss" : "ws";
  WS_LIVE_URL.pathname = `${LIVE_BASE_PATH}/workitem`;
  WS_LIVE_URL.searchParams.set("workspaceSlug", workspaceSlug);
  WS_LIVE_URL.searchParams.set("connectionId", connectionId);
  return WS_LIVE_URL.toString();
};

const disposeEntry = (workspaceSlug: string): void => {
  const entry = socketRegistry.get(workspaceSlug);
  if (!entry) return;
  socketRegistry.delete(workspaceSlug);
  entry.disposed = true;
  if (entry.reconnectTimer) {
    clearTimeout(entry.reconnectTimer);
    entry.reconnectTimer = null;
  }
  if (entry.socket && (entry.socket.readyState === WS_CONNECTING || entry.socket.readyState === WS_OPEN)) {
    entry.socket.close(1000, "component unmounted");
  }
};

const connectWorkspace = (
  workspaceSlug: string,
  onEvent: (payload: TWorkItemRealtimeEvent) => void
): TWorkitemSocketEntry => {
  const entry: TWorkitemSocketEntry = {
    socket: undefined,
    connectionId: generateConnectionId(),
    refCount: 0,
    reconnectAttempt: 0,
    reconnectTimer: null,
    disposed: false,
  };

  const scheduleReconnect = (): void => {
    if (entry.disposed || entry.reconnectTimer) return;
    const delay = Math.min(BASE_RECONNECT_DELAY * 2 ** entry.reconnectAttempt, MAX_RECONNECT_DELAY);
    entry.reconnectAttempt += 1;
    entry.reconnectTimer = setTimeout(() => {
      entry.reconnectTimer = null;
      open();
    }, delay);
  };

  const open = (): void => {
    if (entry.disposed) return;
    const socket = new WebSocket(buildSocketUrl(workspaceSlug, entry.connectionId));
    entry.socket = socket;

    socket.onopen = () => {
      entry.reconnectAttempt = 0;
    };
    socket.onmessage = (messageEvent: MessageEvent) => {
      try {
        const data = JSON.parse(messageEvent.data as string) as {
          type?: string;
          payload?: TWorkItemRealtimeEvent;
        };
        if (data.type === "workitem" && data.payload) onEvent(data.payload);
      } catch {
        // ignore malformed frames
      }
    };
    socket.onclose = (closeEvent: CloseEvent) => {
      if (entry.disposed) return;
      if (NO_RECONNECT_CLOSE_CODES.has(closeEvent.code)) {
        disposeEntry(workspaceSlug);
        return;
      }
      scheduleReconnect();
    };
  };

  open();
  return entry;
};

/**
 * Real-time work-item sync listener.
 *
 * Mounts ONCE at the workspace layout root and keeps a singleton websocket
 * per workspace (auto-reconnect with exponential backoff). Incoming events
 * are patched directly into the issue stores — no refetch.
 */
export const useRealtimeWorkitem = (workspaceSlug: string | undefined): void => {
  const store = useContext(StoreContext);

  useEffect(() => {
    if (!workspaceSlug) return;

    const handleWorkitemEvent = (payload: TWorkItemRealtimeEvent): void => {
      const issueStore = store.issue.issues;
      const issue = issueStore.getIssueById(payload.id);
      if (!issue) return;
      const patch = applyRealtimePatch(issue, payload);
      if (patch) issueStore.updateIssue(issue.id, patch);
    };

    let entry = socketRegistry.get(workspaceSlug);
    if (!entry) {
      entry = connectWorkspace(workspaceSlug, handleWorkitemEvent);
      socketRegistry.set(workspaceSlug, entry);
    }
    entry.refCount += 1;

    return () => {
      entry.refCount -= 1;
      if (entry.refCount <= 0) disposeEntry(workspaceSlug);
    };
    // `store` is a stable singleton provided by StoreProvider.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceSlug]);
};
