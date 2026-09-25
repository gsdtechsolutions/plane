# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from rest_framework import serializers

from plane.app.serializers.base import BaseSerializer
from plane.db.models import TimeEntry


class TimeEntrySerializer(BaseSerializer):
    """Serializer for TimeEntry.

    project/issue/user are never taken from the payload: the issue-scoped
    view injects them from the URL and the authenticated user on create,
    and they are read-only afterwards so an entry can never be moved
    across issues or attributed to someone else.
    """

    minutes = serializers.IntegerField(min_value=0, required=False)

    class Meta:
        model = TimeEntry
        fields = "__all__"
        read_only_fields = [
            "id",
            "created_at",
            "updated_at",
            "deleted_at",
            "workspace",
            "project",
            "issue",
            "user",
            "created_by",
            "updated_by",
        ]
