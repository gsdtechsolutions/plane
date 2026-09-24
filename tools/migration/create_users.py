# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Pre-create user accounts on the target Plane database from the source Plane
workspace members, so migrated work items can be assigned to the right people.

Must run inside the target Plane API container (it writes via the Django ORM)
but reads the source workspace members over the REST API. Configuration comes
from CLI arguments (when run as a file) or MIGRATION_* environment variables.
"""

import argparse
import os
import sys
import django
import requests

# Setup Django environment inside the container
sys.path.append("/app")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "plane.settings.production")
django.setup()

from plane.db.models import User, Workspace, WorkspaceMember

# CONFIGURATION — CLI arguments take precedence over MIGRATION_* environment variables
_parser = argparse.ArgumentParser(
    description="Pre-create target Plane user accounts from source Plane workspace members."
)
_parser.add_argument("--source-url", "--old-url", dest="old_plane_url", default=None,
                     help="Source Plane instance base URL (env: MIGRATION_OLD_PLANE_URL)")
_parser.add_argument("--source-token", "--old-token", dest="old_api_token", default=None,
                     help="Source Plane API token (env: MIGRATION_OLD_API_TOKEN)")
_parser.add_argument("--source-workspace", "--old-workspace", dest="old_workspace_slug", default=None,
                     help="Source workspace slug (env: MIGRATION_OLD_WORKSPACE_SLUG)")
_parser.add_argument("--target-workspace", "--new-workspace", dest="new_workspace_slug", default=None,
                     help="Target workspace slug (env: MIGRATION_NEW_WORKSPACE_SLUG)")
_args, _ = _parser.parse_known_args()

OLD_PLANE_URL = (_args.old_plane_url or os.getenv("MIGRATION_OLD_PLANE_URL", "")).rstrip("/")
OLD_API_TOKEN = _args.old_api_token or os.getenv("MIGRATION_OLD_API_TOKEN", "")
OLD_WORKSPACE_SLUG = _args.old_workspace_slug or os.getenv("MIGRATION_OLD_WORKSPACE_SLUG", "")

NEW_WORKSPACE_SLUG = _args.new_workspace_slug or os.getenv("MIGRATION_NEW_WORKSPACE_SLUG", "")
DEFAULT_PASSWORD = "TemporaryOrca123!"

old_headers = {
    "Authorization": f"Bearer {OLD_API_TOKEN}",
    "x-api-key": OLD_API_TOKEN,
    "Content-Type": "application/json",
}


def fetch_users_from_old_plane():
    print(f"[+] Fetching workspace members from old Plane ({OLD_PLANE_URL})...")
    try:
        res = requests.get(
            f"{OLD_PLANE_URL}/api/v1/workspaces/{OLD_WORKSPACE_SLUG}/members/",
            headers=old_headers,
        )
        res.raise_for_status()
        members = res.json()
        
        users_list = []
        for m in members:
            email = m.get("member", {}).get("email") or m.get("email")
            first_name = m.get("member", {}).get("first_name", "") or m.get("first_name", "")
            last_name = m.get("member", {}).get("last_name", "") or m.get("last_name", "")
            role = m.get("role", 15)
            if email:
                users_list.append((email, first_name, last_name, role))
        
        return users_list
    except Exception as e:
        print(f"[-] Failed to fetch users: {e}")
        return []


def create_users():
    if not OLD_PLANE_URL or not OLD_API_TOKEN or not NEW_WORKSPACE_SLUG:
        print("[-] Error: Provide MIGRATION_OLD_PLANE_URL, MIGRATION_OLD_API_TOKEN, and MIGRATION_NEW_WORKSPACE_SLUG via CLI arguments or .env")
        return

    users_to_create = fetch_users_from_old_plane()
    if not users_to_create:
        print("[-] No users found to pre-create.")
        return

    try:
        workspace = Workspace.objects.get(slug=NEW_WORKSPACE_SLUG)
    except Workspace.DoesNotExist:
        print(f"[-] Workspace with slug '{NEW_WORKSPACE_SLUG}' does not exist on target database.")
        return

    for email, first_name, last_name, role in users_to_create:
        user, created = User.objects.get_or_create(
            email=email,
            defaults={
                "first_name": first_name,
                "last_name": last_name,
                "username": email.split("@")[0],
                "is_active": True,
            }
        )
        if created:
            user.set_password(DEFAULT_PASSWORD)
            user.save()
            print(f"[+] Created user account: {email}")
        else:
            print(f"[-] User account {email} already exists.")

        # Associate with workspace
        member, member_created = WorkspaceMember.objects.get_or_create(
            workspace=workspace,
            member=user,
            defaults={"role": role}
        )
        if member_created:
            print(f"    [+] Added {email} to workspace '{NEW_WORKSPACE_SLUG}' with role {role}.")
        else:
            print(f"    [-] {email} is already a member of workspace.")


if __name__ == "__main__":
    create_users()
