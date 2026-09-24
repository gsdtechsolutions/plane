/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import type { Request } from "express";
import type WebSocket from "ws";
// plane imports
import { Controller, WebSocket as WSDecorator } from "@plane/decorators";
import { logger } from "@plane/logger";
import axios from "axios";
// env
import { env } from "@/env";
// extensions
import { workitemEventFanout } from "@/extensions/workitem-events";
// services
import { UserService } from "@/services/user.service";

const WS_CLOSE_BAD_REQUEST = 4400;
const WS_CLOSE_AUTH_FAILED = 4401;
const WS_CLOSE_INTERNAL = 1011;

/**
 * Real-time work-item sync endpoint.
 *
 * Every viewer of a workspace's work items opens this websocket (cookies
 * authenticate the user, same-origin), and receives curated work-item events
 * published by the Django API on the gsd:workitem-events Redis channel.
 */
@Controller("/workitem")
export class WorkitemController {
  constructor() {
    // Controllers are constructed at server boot (see Server.setupRoutes) —
    // start the Redis subscriber alongside them.
    void workitemEventFanout.start();
  }

  @WSDecorator("/")
  async handleConnection(ws: WebSocket, req: Request) {
    try {
      const requestUrl = new URL(req.url ?? "/", `http://${req.headers.host ?? "localhost"}`);
      const workspaceSlug = requestUrl.searchParams.get("workspaceSlug");
      const connectionId = requestUrl.searchParams.get("connectionId");

      if (!workspaceSlug) {
        ws.close(WS_CLOSE_BAD_REQUEST, "workspaceSlug is required");
        return;
      }

      const cookie = req.headers.cookie?.toString();
      if (!cookie) {
        ws.close(WS_CLOSE_AUTH_FAILED, "Authentication required");
        return;
      }

      // Authenticate the user from the session cookie (mirrors lib/auth.ts).
      const userService = new UserService();
      const user = await userService.currentUser(cookie);

      // Validate the user is a member of the workspace before joining the room.
      await this.assertWorkspaceMembership(cookie, workspaceSlug);

      workitemEventFanout.register(ws, { workspaceSlug, userId: user.id, connectionId });
      ws.send(
        JSON.stringify({
          type: "connected",
          workspaceSlug,
          connectionId: connectionId ?? null,
        })
      );

      ws.on("close", () => {
        workitemEventFanout.unregister(ws);
      });
      ws.on("error", (error: Error) => {
        logger.error("WORKITEM_CONTROLLER: WebSocket connection error:", error);
        workitemEventFanout.unregister(ws);
        ws.close(WS_CLOSE_INTERNAL, "Internal server error");
      });
    } catch (error) {
      logger.error("WORKITEM_CONTROLLER: WebSocket connection rejected:", error);
      workitemEventFanout.unregister(ws);
      ws.close(WS_CLOSE_AUTH_FAILED, "Authentication failed");
    }
  }

  /**
   * The Django API enforces workspace membership on this endpoint; a 2xx
   * response for the session cookie proves the user can see the workspace.
   */
  private async assertWorkspaceMembership(cookie: string, workspaceSlug: string): Promise<void> {
    await axios.get(`${env.API_BASE_URL}/api/workspaces/${workspaceSlug}/workspace-members/me/`, {
      headers: { Cookie: cookie },
      timeout: 15000,
    });
  }
}
