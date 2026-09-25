# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Fork feature: issue time tracking (TimeEntry sidecar table + running timer).

Modules:
    serializers: TimeEntrySerializer with create/update constraints.
    api: issue-scoped CRUD, issue summary, workspace-wide list, and the
        per-user timer start/stop endpoints.
"""
