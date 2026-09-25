# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import re
from datetime import timedelta
from urllib.parse import quote
from uuid import UUID

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.exceptions import ValidationError

from plane.db.models import Issue
from plane.db.models.github_delivery import (
    GitHubConnection,
    GitHubRepositoryMapping,
    GitHubPullRequest,
    GitHubIssueLink,
    GitHubRelease,
    GitHubWebhookDelivery,
)
from .client import web_base


def positive_id(value):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValidationError("GitHub returned an invalid identifier.")
    return value


def repository_name(value):
    if not isinstance(value, str) or len(value) > 255 or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value):
        raise ValidationError("GitHub returned an invalid repository name.")
    if any(part in (".", "..") for part in value.split("/")):
        raise ValidationError("GitHub returned an invalid repository name.")
    return value


def timestamp(value):
    if not isinstance(value, str):
        return None
    result = parse_datetime(value)
    if result and timezone.is_naive(result):
        result = timezone.make_aware(result)
    return result


def text(value, limit):
    return value[:limit] if isinstance(value, str) else ""


def iso(value):
    return value.isoformat() if value else None


def pull_request_data(pr, *, manual=False):
    return {
        "id": str(pr.id),
        "repository": pr.mapping.full_name,
        "repository_id": pr.mapping.repository_id,
        "number": pr.number,
        "title": pr.title,
        "url": f"{web_base(pr.mapping.connection.host)}/{pr.mapping.full_name}/pull/{pr.number}",
        "state": pr.state,
        "draft": pr.draft,
        "merged_at": iso(pr.merged_at),
        "review_state": pr.review_state,
        "linked_manually": manual,
        "connected": pr.mapping.is_active and pr.mapping.connection.is_active,
    }


def release_data(release):
    return {
        "id": str(release.id),
        "repository": release.mapping.full_name,
        "repository_id": release.mapping.repository_id,
        "tag_name": release.tag_name,
        "name": release.name,
        "url": f"{web_base(release.mapping.connection.host)}/{release.mapping.full_name}/releases/tag/{quote(release.tag_name, safe='')}",
        "draft": release.draft,
        "prerelease": release.prerelease,
        "published_at": iso(release.published_at),
        "body": release.body,
    }


def project_pull_requests(project):
    return GitHubPullRequest.objects.filter(
        mapping__project=project, mapping__connection__workspace_id=project.workspace_id
    )


def list_issue_pull_requests(issue):
    links = (
        GitHubIssueLink.objects.filter(
            issue=issue,
            is_suppressed=False,
            pull_request__mapping__project_id=issue.project_id,
            pull_request__mapping__connection__workspace_id=issue.workspace_id,
        )
        .select_related("pull_request__mapping__connection")
        .order_by("-pull_request__updated_at")
    )
    return [pull_request_data(link.pull_request, manual=link.is_manual) for link in links]


def list_project_releases(project):
    releases = (
        GitHubRelease.objects.filter(
            mapping__project=project,
            mapping__connection__workspace_id=project.workspace_id,
            is_deleted=False,
        )
        .select_related("mapping", "mapping__connection")
        .order_by("-published_at", "-updated_at")[:200]
    )
    return [release_data(release) for release in releases]


def validate_project_pull_requests(project, ids):
    try:
        wanted = {UUID(str(value)) for value in ids}
    except (ValueError, TypeError, AttributeError):
        return False
    return len(wanted) <= 100 and project_pull_requests(project).filter(pk__in=wanted).count() == len(wanted)


def get_release_sources(project, pr_ids, release_id=None):
    if len(pr_ids) > 100 or not validate_project_pull_requests(project, set(pr_ids)):
        raise ValidationError("Select pull requests from this project only (up to 100).")
    sources = []
    for pr in (
        project_pull_requests(project).filter(pk__in=pr_ids).select_related("mapping__connection").order_by("number")
    ):
        sources.append(
            {
                "type": "pull_request",
                "id": str(pr.id),
                "title": pr.title,
                "text": pr.body[:20000],
                "url": pull_request_data(pr)["url"],
            }
        )
    if release_id:
        try:
            release = GitHubRelease.objects.select_related("mapping").get(
                id=release_id,
                mapping__project=project,
                mapping__connection__workspace_id=project.workspace_id,
                is_deleted=False,
            )
        except (GitHubRelease.DoesNotExist, ValueError, TypeError, DjangoValidationError) as error:
            raise ValidationError("Select a GitHub release from this project.") from error
        sources.append(
            {
                "type": "github_release",
                "id": str(release.id),
                "title": release.name or release.tag_name,
                "text": release.body[:20000],
                "url": release_data(release)["url"],
            }
        )
    return sources


def reconcile_links(pr):
    project = pr.mapping.project
    pattern = rf"(?<![A-Za-z0-9_]){re.escape(project.identifier)}-(\d+)(?![A-Za-z0-9_])"
    values = {
        int(match)
        for match in re.findall(pattern, f"{pr.title}\n{pr.body}\n{pr.head_ref}", re.IGNORECASE)
        if len(match) <= 10
    }
    issues = Issue.objects.filter(
        project=project, workspace_id=pr.mapping.connection.workspace_id, sequence_id__in=sorted(values)[:100]
    )
    issue_ids = set(issues.values_list("id", flat=True))
    GitHubIssueLink.objects.filter(pull_request=pr, is_manual=False, is_suppressed=False).exclude(
        issue_id__in=issue_ids
    ).delete()
    for issue_id in issue_ids:
        GitHubIssueLink.objects.get_or_create(pull_request=pr, issue_id=issue_id)


def upsert_pull_request(mapping, data):
    if not isinstance(data, dict):
        raise ValidationError("Invalid pull request payload.")
    if positive_id(data.get("base", {}).get("repo", {}).get("id")) != mapping.repository_id:
        raise ValidationError("Pull request repository does not match its mapping.")
    number = positive_id(data.get("number"))
    github_id = positive_id(data.get("id"))
    updated = timestamp(data.get("updated_at"))
    if not updated:
        raise ValidationError("Pull request update time is required.")
    pr, created = GitHubPullRequest.objects.get_or_create(
        mapping=mapping, number=number, defaults={"github_id": github_id}
    )
    if not created and pr.remote_updated_at and updated < pr.remote_updated_at:
        return pr
    pr.github_id = github_id
    pr.title = text(data.get("title"), 1000)
    pr.body = text(data.get("body"), 20000)
    pr.head_ref = text(data.get("head", {}).get("ref"), 500)
    pr.merged_at = timestamp(data.get("merged_at"))
    pr.state = (
        "merged"
        if pr.merged_at or data.get("merged") is True
        else "closed"
        if data.get("state") == "closed"
        else "open"
    )
    pr.draft = data.get("draft") is True
    pr.remote_updated_at = updated
    pr.save()
    reconcile_links(pr)
    return pr


def upsert_release(mapping, data, *, deleted=False, received_at=None):
    if not isinstance(data, dict):
        raise ValidationError("Invalid release payload.")
    github_id = positive_id(data.get("id"))
    remote = timestamp(data.get("updated_at")) or received_at or timezone.now()
    release, created = GitHubRelease.objects.get_or_create(mapping=mapping, github_id=github_id)
    if not created and release.remote_updated_at and remote < release.remote_updated_at:
        return release
    release.tag_name = text(data.get("tag_name"), 255)
    release.name = text(data.get("name"), 1000)
    release.body = text(data.get("body"), 20000)
    release.draft = data.get("draft") is True
    release.prerelease = data.get("prerelease") is True
    release.published_at = timestamp(data.get("published_at"))
    release.is_deleted = deleted
    release.remote_updated_at = remote
    release.save()
    return release


DELIVERY_RETENTION = timedelta(hours=24)
MAX_PROCESSING_ATTEMPTS = 5
CONTENT_EVENTS = {"pull_request", "pull_request_review", "release"}


def process_delivery(delivery_id):
    with transaction.atomic():
        delivery = GitHubWebhookDelivery.objects.select_for_update().get(id=delivery_id)
        if delivery.status in ("processed", "ignored", "failed"):
            return
        now = timezone.now()
        # Deliveries accepted before migration 0149 retain their original binding.
        # Populate only the new lookup fields from their authenticated stored body.
        changed = []
        try:
            if delivery.installation_id is None:
                delivery.installation_id = positive_id(delivery.payload.get("installation", {}).get("id"))
                changed.append("installation_id")
            if delivery.event in CONTENT_EVENTS and delivery.repository_id is None:
                delivery.repository_id = positive_id(delivery.payload.get("repository", {}).get("id"))
                changed.append("repository_id")
        except (ValidationError, TypeError, ValueError, AttributeError):
            delivery.status = "failed"
            delivery.error = "InvalidStoredPayload"
            delivery.payload = {}
            delivery.processed_at = now
            delivery.save(update_fields=["status", "error", "payload", "processed_at"])
            return
        if changed:
            delivery.save(update_fields=changed)
        if delivery.received_at < now - DELIVERY_RETENTION:
            delivery.status = "ignored"
            delivery.error = "Expired"
        elif delivery.next_retry_at and delivery.next_retry_at > now:
            return
        else:
            # Only never-bound waiting events may acquire a connection. A deleted
            # or disconnected connection can never rebind an event to a workspace.
            if delivery.connection_id:
                connection = GitHubConnection.objects.select_for_update().filter(id=delivery.connection_id).first()
            elif delivery.status == "waiting":
                # Bind only to a connection of the App that signed the delivery.
                connection = (
                    GitHubConnection.objects.select_for_update()
                    .filter(
                        host=delivery.host,
                        installation_id=delivery.installation_id,
                        app=delivery.app,
                    )
                    .first()
                )
                if not connection:
                    return
                delivery.connection = connection
                delivery.status = "awaiting_mapping"
            else:
                connection = None
            if not connection or not connection.is_active:
                delivery.status = "ignored"
            elif (
                delivery.event in CONTENT_EVENTS
                and not GitHubRepositoryMapping.objects.filter(
                    connection=connection,
                    repository_id=delivery.repository_id,
                    is_active=True,
                    project__workspace_id=connection.workspace_id,
                    project__deleted_at__isnull=True,
                ).exists()
            ):
                # Explicitly disconnected repositories stay disconnected. Only a
                # repository never mapped in this installation can wait for setup.
                if GitHubRepositoryMapping.objects.filter(
                    connection=connection,
                    repository_id=delivery.repository_id,
                ).exists():
                    delivery.status = "ignored"
                else:
                    delivery.status = "awaiting_mapping"
                    delivery.save(update_fields=["connection", "status"])
                    return
            else:
                delivery.processing_attempts += 1
                try:
                    with transaction.atomic():
                        apply_delivery(connection, delivery)
                    delivery.status = "processed"
                    delivery.error = ""
                    delivery.next_retry_at = None
                except (ValidationError, TypeError, ValueError, AttributeError, KeyError) as error:
                    # Retrying identical invalid input cannot repair it.
                    delivery.status = "failed"
                    delivery.error = type(error).__name__
                except Exception as error:
                    # Savepoint rollback removes partial projections before the
                    # durable retry is recorded. Beat also survives worker loss.
                    delivery.status = "retry" if delivery.processing_attempts < MAX_PROCESSING_ATTEMPTS else "failed"
                    delivery.error = type(error).__name__[:100]
                    delivery.next_retry_at = now + timedelta(
                        seconds=min(60 * 2 ** (delivery.processing_attempts - 1), 3600)
                    )
        delivery.processed_at = now
        if delivery.status in ("processed", "ignored", "failed"):
            delivery.payload = {}
            delivery.next_retry_at = None
        delivery.save(
            update_fields=[
                "connection",
                "status",
                "error",
                "processed_at",
                "payload",
                "processing_attempts",
                "next_retry_at",
            ]
        )


def apply_delivery(connection, delivery):
    payload = delivery.payload
    if positive_id(payload.get("installation", {}).get("id")) != connection.installation_id:
        raise ValidationError("Installation mismatch.")
    action = payload.get("action")
    if delivery.event == "installation":
        if action in ("deleted", "suspend"):
            connection.is_active = False
            connection.save(update_fields=["is_active", "updated_at"])
            GitHubRepositoryMapping.objects.filter(connection=connection).update(is_active=False)
        return
    if delivery.event == "installation_repositories":
        removed = [positive_id(repo.get("id")) for repo in payload.get("repositories_removed", [])]
        GitHubRepositoryMapping.objects.filter(connection=connection, repository_id__in=removed).update(is_active=False)
        return
    repository_id = positive_id(payload.get("repository", {}).get("id"))
    mapping = (
        GitHubRepositoryMapping.objects.select_for_update()
        .filter(
            connection=connection,
            repository_id=repository_id,
            is_active=True,
            project__workspace_id=connection.workspace_id,
            project__deleted_at__isnull=True,
        )
        .select_related("project", "connection")
        .first()
    )
    if not mapping:
        return
    if delivery.event in ("pull_request", "pull_request_review"):
        pr = upsert_pull_request(mapping, payload.get("pull_request"))
        if delivery.event == "pull_request_review":
            review = payload.get("review", {})
            reviewed = timestamp(review.get("submitted_at")) or delivery.received_at
            if not pr.reviewed_at or reviewed >= pr.reviewed_at:
                pr.review_state = "pending" if action == "dismissed" else review.get("state", "pending").lower()
                if pr.review_state not in ("approved", "changes_requested", "commented"):
                    pr.review_state = "pending"
                pr.reviewed_at = reviewed
                pr.save(update_fields=["review_state", "reviewed_at", "updated_at"])
    elif delivery.event == "release":
        upsert_release(mapping, payload.get("release"), deleted=action == "deleted", received_at=delivery.received_at)


def sync_mapping(mapping_id):
    from .client import GitHubClient

    mapping = (
        GitHubRepositoryMapping.objects.select_related("connection", "project")
        .filter(
            id=mapping_id,
            is_active=True,
            connection__is_active=True,
        )
        .first()
    )
    if not mapping:
        return
    fetched_at = timezone.now()
    pulls, releases = GitHubClient(mapping.connection.host, mapping.connection.app).recent_items(mapping)
    if not isinstance(pulls, list) or not isinstance(releases, list):
        raise ValidationError("GitHub returned invalid repository data.")
    with transaction.atomic():
        connection = GitHubConnection.objects.select_for_update().get(id=mapping.connection_id)
        mapping = (
            GitHubRepositoryMapping.objects.select_for_update()
            .select_related("connection", "project")
            .get(id=mapping_id)
        )
        if not connection.is_active or not mapping.is_active or mapping.project.workspace_id != connection.workspace_id:
            return
        for data in pulls[:100]:
            upsert_pull_request(mapping, data)
        for data in releases[:100]:
            upsert_release(mapping, data, received_at=fetched_at)
        mapping.sync_status = "synced"
        mapping.sync_error = ""
        mapping.last_synced_at = timezone.now()
        mapping.save(update_fields=["sync_status", "sync_error", "last_synced_at"])
