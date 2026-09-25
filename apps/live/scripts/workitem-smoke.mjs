/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

/**
 * Smoke test for the real-time work-item sync pipeline (live service).
 *
 * Boots the compiled live server (apps/live/dist) against a mock Django API,
 * joins two websocket clients to a workspace room, publishes fake events to
 * the gsd:workitem-events Redis channel and asserts delivery, self-echo
 * suppression and workspace isolation.
 *
 * Usage (from apps/live, after `pnpm -F live build`):
 *   node scripts/workitem-smoke.mjs
 */

import { createServer } from "node:http";
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import Redis from "ioredis";
import WebSocket from "ws";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const LIVE_DIR = path.resolve(__dirname, "..");
const DIST_ENTRY = path.join(LIVE_DIR, "dist", "start.mjs");

const MOCK_API_PORT = Number(process.env.SMOKE_MOCK_API_PORT || 3917);
const LIVE_PORT = Number(process.env.SMOKE_LIVE_PORT || 3918);
const REDIS_URL = process.env.REDIS_URL || "redis://127.0.0.1:6379";
const WORKSPACE_SLUG = "smoke-ws";
const OTHER_WORKSPACE_SLUG = "smoke-other-ws";
const CHANNEL = "gsd:workitem-events";
const SETTLE_MS = 700;

if (!existsSync(DIST_ENTRY)) {
  console.error(`✗ ${DIST_ENTRY} not found — run \`pnpm -F live build\` first.`);
  process.exit(1);
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// ---------------------------------------------------------------------------
// Mock Django API: /api/users/me/ (auth) + workspace-members/me (membership)
// ---------------------------------------------------------------------------
const mockApi = createServer((req, res) => {
  const url = new URL(req.url ?? "/", `http://127.0.0.1:${MOCK_API_PORT}`);
  res.setHeader("Content-Type", "application/json");

  if (url.pathname === "/api/users/me/") {
    res.end(JSON.stringify({ id: "smoke-user-id" }));
    return;
  }
  if (url.pathname === `/api/workspaces/${WORKSPACE_SLUG}/workspace-members/me/`) {
    res.end(JSON.stringify({ id: "smoke-user-id" }));
    return;
  }
  res.statusCode = 404;
  res.end(JSON.stringify({ error: "not found" }));
});

const startMockApi = () =>
  new Promise((resolve, reject) => {
    mockApi.once("error", reject);
    mockApi.listen(MOCK_API_PORT, "127.0.0.1", () => resolve());
  });

// ---------------------------------------------------------------------------
// Live server process
// ---------------------------------------------------------------------------
let liveServer = null;
let liveServerLogs = "";

const startLiveServer = async () => {
  liveServer = spawn("node", [DIST_ENTRY], {
    cwd: LIVE_DIR,
    env: {
      ...process.env,
      PORT: String(LIVE_PORT),
      API_BASE_URL: `http://127.0.0.1:${MOCK_API_PORT}`,
      LIVE_SERVER_SECRET_KEY: "smoke-secret",
      REDIS_URL,
      LIVE_BASE_PATH: "/live",
      HOSTNAME: "smoke-live",
    },
    stdio: ["ignore", "pipe", "pipe"],
  });
  const onLog = (chunk) => {
    liveServerLogs += chunk.toString();
  };
  liveServer.stdout.on("data", onLog);
  liveServer.stderr.on("data", onLog);

  // Wait until the HTTP server answers (any status proves it is listening).
  const deadline = Date.now() + 30000;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(`http://127.0.0.1:${LIVE_PORT}/live/health`);
      if (res.status > 0) return;
    } catch {
      // not up yet
    }
    await sleep(200);
  }
  throw new Error(`live server did not start:\n${liveServerLogs}`);
};

// ---------------------------------------------------------------------------
// WebSocket helpers
// ---------------------------------------------------------------------------
const openSocket = (label, connectionId) =>
  new Promise((resolve, reject) => {
    const socket = new WebSocket(
      `ws://127.0.0.1:${LIVE_PORT}/live/workitem?workspaceSlug=${WORKSPACE_SLUG}&connectionId=${connectionId}`,
      { headers: { Cookie: "sessionid=smoke-session" } }
    );
    const received = [];
    const timeout = setTimeout(() => reject(new Error(`${label}: connect timeout`)), 5000);
    socket.on("message", (raw) => {
      try {
        received.push(JSON.parse(raw.toString()));
      } catch {
        // ignore malformed frames
      }
    });
    socket.on("error", (error) => {
      clearTimeout(timeout);
      reject(new Error(`${label}: ${error.message}`));
    });
    socket.on("close", (code) => {
      if (timeout && received.length === 0) reject(new Error(`${label}: closed early (${code})`));
    });
    socket.once("message", (raw) => {
      const data = JSON.parse(raw.toString());
      if (data.type !== "connected") {
        clearTimeout(timeout);
        reject(new Error(`${label}: expected first message "connected", got ${JSON.stringify(data)}`));
        return;
      }
      clearTimeout(timeout);
      resolve({ label, socket, received });
    });
  });

const publishEvent = async (redis, event) => {
  const receivers = await redis.publish(CHANNEL, JSON.stringify(event));
  await sleep(SETTLE_MS);
  return receivers;
};

const eventsFor = (client, id) => client.received.filter((m) => m.type === "workitem" && m.payload?.id === id);

// ---------------------------------------------------------------------------
// Test flow
// ---------------------------------------------------------------------------
const results = [];
const assert = (name, condition, detail = "") => {
  results.push({ name, ok: condition, detail });
  console.log(`${condition ? "✓" : "✗"} ${name}${condition ? "" : ` — ${detail}`}`);
};

const run = async () => {
  await startMockApi();
  await startLiveServer();

  const redis = new Redis(REDIS_URL);
  await sleep(500); // give the live server's redis subscriber a beat

  const c1 = await openSocket("client-1", "smoke-conn-1");
  const c2 = await openSocket("client-2", "smoke-conn-2");
  console.log("both clients joined the workspace room");

  // A: broadcast reaches every client in the room
  await publishEvent(redis, { workspace_slug: WORKSPACE_SLUG, id: "issue-a", action: "updated", name: "A" });
  assert("broadcast reaches both clients", eventsFor(c1, "issue-a").length === 1 && eventsFor(c2, "issue-a").length === 1);

  // B: self-echo suppression via connection_id
  await publishEvent(redis, {
    workspace_slug: WORKSPACE_SLUG,
    id: "issue-b",
    action: "updated",
    name: "B",
    connection_id: "smoke-conn-1",
  });
  assert(
    "self-echo suppressed for originating connection",
    eventsFor(c1, "issue-b").length === 0 && eventsFor(c2, "issue-b").length === 1
  );

  // C: workspace isolation
  await publishEvent(redis, { workspace_slug: OTHER_WORKSPACE_SLUG, id: "issue-c", action: "updated", name: "C" });
  assert(
    "events stay scoped to their workspace",
    eventsFor(c1, "issue-c").length === 0 && eventsFor(c2, "issue-c").length === 0
  );

  // D: full payload round-trip keeps the curated fields intact
  await publishEvent(redis, {
    workspace_slug: WORKSPACE_SLUG,
    id: "issue-d",
    workspace_slug_check: true,
    project_id: "proj-1",
    sequence_id: 7,
    name: "D",
    priority: "high",
    state_id: "state-1",
    assignee_ids: ["u1", "u2"],
    label_ids: ["l1"],
    action: "updated",
    timestamp: new Date().toISOString(),
  });
  const d = eventsFor(c1, "issue-d")[0]?.payload;
  assert(
    "curated payload fields survive the fanout",
    !!d && d.priority === "high" && d.state_id === "state-1" && d.assignee_ids.length === 2 && d.sequence_id === 7,
    JSON.stringify(d ?? null)
  );

  c1.socket.close();
  c2.socket.close();

  const failed = results.filter((r) => !r.ok);
  console.log(`\n${results.length - failed.length}/${results.length} assertions passed`);
  if (failed.length > 0) {
    console.error(`\n--- live server logs ---\n${liveServerLogs.slice(-4000)}`);
    process.exitCode = 1;
  }
};

const teardown = () => {
  try {
    liveServer?.kill("SIGTERM");
  } catch {
    // already dead
  }
  try {
    mockApi.close();
  } catch {
    // already closed
  }
};

run()
  .catch((error) => {
    console.error(`✗ smoke test failed: ${error.message}`);
    if (liveServerLogs) console.error(`\n--- live server logs ---\n${liveServerLogs.slice(-4000)}`);
    process.exitCode = 1;
  })
  .finally(async () => {
    teardown();
    await sleep(300);
    process.exit(process.exitCode ?? 0);
  });
