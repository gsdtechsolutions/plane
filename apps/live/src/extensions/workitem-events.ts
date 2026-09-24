/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import type WebSocket from "ws";
import Redis from "ioredis";
import { logger } from "@plane/logger";
// redis
import { redisManager } from "@/redis";

// Channel the Django API publishes curated work-item events to (see
// apps/api/plane/app/serializers/workitem_realtime.py).
export const WORKITEM_EVENTS_CHANNEL = "gsd:workitem-events";

export type TWorkitemClientMeta = {
  workspaceSlug: string;
  userId: string;
  connectionId?: string | null;
};

type TWorkitemClient = TWorkitemClientMeta & {
  ws: WebSocket;
};

// Shape of the payload Django publishes on WORKITEM_EVENTS_CHANNEL.
export type TWorkitemRedisEvent = {
  workspace_slug?: string;
  connection_id?: string | null;
  [key: string]: unknown;
};

// WebSocket open ready-state constant (ws.Socket.OPEN is not on instances).
const WS_OPEN = 1;

/**
 * Subscribes to the Django work-item events channel and fans events out to the
 * websocket clients connected to this live server instance for that workspace.
 *
 * The subscriber runs on a dedicated Redis connection (a connection in
 * subscribe mode cannot issue regular commands), duplicated from the shared
 * manager client so it inherits the REDIS_URL/REDIS_HOST handling.
 */
export class WorkitemEventFanout {
  private readonly clients = new Map<WebSocket, TWorkitemClient>();
  private subscriber: Redis | null = null;
  private starting: Promise<void> | null = null;

  /**
   * Idempotently start the Redis subscriber. Called when the work-item
   * controller is constructed at server boot.
   */
  public async start(): Promise<void> {
    if (this.subscriber) return;
    if (this.starting) {
      await this.starting;
      return;
    }

    this.starting = this.connectSubscriber();
    try {
      await this.starting;
    } catch (error) {
      logger.error("WORKITEM_EVENTS: Failed to start Redis subscriber:", error);
    } finally {
      this.starting = null;
    }
  }

  private async connectSubscriber(): Promise<void> {
    const baseClient = redisManager.getClient();
    if (!baseClient) {
      logger.warn("WORKITEM_EVENTS: Redis client unavailable, work-item fanout disabled");
      return;
    }

    const subscriber = baseClient.duplicate();
    subscriber.on("error", (error: Error) => {
      logger.error("WORKITEM_EVENTS: Redis subscriber error:", error);
    });

    await subscriber.subscribe(WORKITEM_EVENTS_CHANNEL);
    subscriber.on("message", this.handleMessage);

    this.subscriber = subscriber;
    logger.info(`WORKITEM_EVENTS: Subscribed to channel ${WORKITEM_EVENTS_CHANNEL}`);
  }

  /**
   * Register a websocket client for its workspace room.
   */
  public register(ws: WebSocket, meta: TWorkitemClientMeta): void {
    this.clients.set(ws, { ...meta, ws });
  }

  /**
   * Remove a websocket client (on close/error).
   */
  public unregister(ws: WebSocket): void {
    this.clients.delete(ws);
  }

  public get clientCount(): number {
    return this.clients.size;
  }

  private handleMessage = (channel: string, message: string): void => {
    if (channel !== WORKITEM_EVENTS_CHANNEL) return;

    let event: TWorkitemRedisEvent;
    try {
      event = JSON.parse(message) as TWorkitemRedisEvent;
    } catch {
      logger.warn("WORKITEM_EVENTS: Dropping malformed event payload");
      return;
    }

    const workspaceSlug = event.workspace_slug;
    if (!workspaceSlug) return;

    for (const [ws, client] of this.clients) {
      if (client.workspaceSlug !== workspaceSlug) continue;
      // Self-echo suppression: a client carrying the connectionId an event
      // originated from does not receive it back.
      if (event.connection_id && client.connectionId && event.connection_id === client.connectionId) continue;
      if (ws.readyState !== WS_OPEN) continue;
      try {
        ws.send(JSON.stringify({ type: "workitem", payload: event }));
      } catch (error) {
        logger.error("WORKITEM_EVENTS: Failed to deliver event to client:", error);
      }
    }
  };

  /**
   * Stop the subscriber (graceful shutdown helper).
   */
  public async stop(): Promise<void> {
    const subscriber = this.subscriber;
    this.subscriber = null;
    if (!subscriber) return;

    try {
      await subscriber.unsubscribe(WORKITEM_EVENTS_CHANNEL);
    } catch (error) {
      logger.warn("WORKITEM_EVENTS: Error unsubscribing from channel:", error);
    }
    subscriber.disconnect();
    logger.info("WORKITEM_EVENTS: Redis subscriber stopped");
  }
}

// Singleton shared between the controller (client registry) and the Redis
// subscriber lifecycle.
export const workitemEventFanout = new WorkitemEventFanout();
