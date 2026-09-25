# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Fork feature: per-project board automations (WHEN trigger THEN actions).

Modules:
    executor: signal receivers that evaluate active rules after commit and
        apply actions with loop-guard + idempotency (import-safe pre
        django.setup(); registered from plane/urls.py and plane/celery.py).
    serializers: AutomationRuleSerializer with save-time validation.
    api: AutomationRuleViewSet (CRUD) + AutomationRuleToggleEndpoint.
"""
