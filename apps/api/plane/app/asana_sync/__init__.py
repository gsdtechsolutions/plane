# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Fork feature: Asana <-> Plane bidirectional sync (user-requested; the Asana
importer's mapping semantics applied as a continuous sync).

Modules:
    client: Asana REST client (PAT auth, pagination, bounded retries).
    crypto: Fernet-at-rest encryption for personal access tokens.
    mapping: pure Asana<->Plane field converters (html_notes, dates, completion).
    engine: pull/push passes with task/comment link tables, LWW conflict
        handling and loop prevention.
    tasks: celery tick/run/cleanup tasks (registered from plane/celery.py).
    webhook: public Asana webhook receiver (handshake + HMAC verification).
    serializers / api: DRF layer (connections are workspace-admin scoped,
        project syncs project-admin scoped; PAT never leaves the server).
"""
