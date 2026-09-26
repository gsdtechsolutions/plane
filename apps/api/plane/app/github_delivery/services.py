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

from plane.db.models import Issue, Project
from plane.db.models.github_delivery import (
    GitHubCommit,
    GitHubCommitIssueLink,
    GitHubConnection,
    GitHubMentionSearch,
    GitHubProjectAutomation,
    GitHubRepositoryMapping,
    GitHubPullRequest,
    GitHubIssueLink,
    GitHubRelease,
    GitHubWebhookDelivery,
)
from plane.utils.exception_logger import log_exception
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


SHA_PATTERN = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")


def commit_data(commit, mapping=None):
    url = ""
    if commit.mapping_id:
        mapping = mapping or commit.mapping
        url = f"{web_base(mapping.connection.host)}/{mapping.full_name}/commit/{commit.sha}"
    return {
        "id": str(commit.id),
        "sha": commit.sha,
        "short_sha": commit.sha[:7],
        "message": commit.message[:2000],
        "author": commit.author_login or commit.author_name,
        "committed_at": iso(commit.committed_at),
        "url": url,
        "connected": bool(
            mapping
            and mapping.connection.is_active
            and mapping.is_active
        ),
    }


def upsert_commit(mapping, data):
    """Store a commit and link it to every work item key it mentions.

    Accepts both push-webhook commit objects (id/message/author/timestamp) and
    search-results commit objects (sha/commit.author/html_url). Commits are
    immutable: an existing row is only filled in, never rewritten.
    """
    if not isinstance(data, dict):
        raise ValidationError("Invalid commit payload.")
    sha = data.get("sha") or data.get("id")
    if not isinstance(sha, str) or not SHA_PATTERN.fullmatch(sha):
        raise ValidationError("Commit signature is required.")
    inner = data.get("commit") if isinstance(data.get("commit"), dict) else {}
    author = inner.get("author") if isinstance(inner.get("author"), dict) else {}
    github_author = data.get("author") if isinstance(data.get("author"), dict) else {}
    message = text(data.get("message") or inner.get("message"), 20000)
    created = False
    commit = GitHubCommit.objects.filter(mapping=mapping, sha=sha).first()
    if commit is None:
        # Push events carry {name, email, username}; search results carry a
        # top-level author {login} plus commit.author {name, date}.
        commit = GitHubCommit.objects.create(
            mapping=mapping,
            sha=sha,
            message=message,
            author_name=text(
                author.get("name") or github_author.get("name") or github_author.get("login") or github_author.get("username"),
                255,
            ),
            author_login=text(github_author.get("login") or github_author.get("username"), 100),
            committed_at=timestamp(author.get("date") or data.get("timestamp")),
        )
        created = True
    if created:
        issue_ids = issues_for_keys(
            mapping.project, mapping.connection.workspace_id, message or ""
        )
        for issue_id in issue_ids:
            GitHubCommitIssueLink.objects.get_or_create(commit=commit, issue_id=issue_id)
    return commit


def list_issue_commits(issue):
    links = (
        GitHubCommitIssueLink.objects.filter(
            issue=issue,
            commit__mapping__project_id=issue.project_id,
            commit__mapping__connection__workspace_id=issue.workspace_id,
        )
        .select_related("commit__mapping__connection")
        .order_by("-commit__committed_at", "-commit__created_at")[:200]
    )
    return [commit_data(link.commit, link.commit.mapping) for link in links]


def mention_search_state(issue):
    marker = GitHubMentionSearch.objects.filter(issue=issue).first()
    if not marker:
        return {"running": False, "searched_at": None, "error": ""}
    return {
        "running": marker.started_at is not None and marker.completed_at is None,
        "searched_at": iso(marker.completed_at),
        "error": marker.error,
    }


MENTION_SEARCH_COOLDOWN = timedelta(minutes=15)
MENTION_SEARCH_PR_FETCH_LIMIT = 20


def queue_mention_search(issue):
    """Claim the per-issue mention-search cooldown; True when a search may run."""
    with transaction.atomic():
        marker, _ = GitHubMentionSearch.objects.select_for_update().get_or_create(issue=issue)
        if marker.completed_at and timezone.now() - marker.completed_at < MENTION_SEARCH_COOLDOWN:
            return False
        # A claim older than the search window means the task was lost; reclaim it.
        if marker.started_at and marker.completed_at is None and timezone.now() - marker.started_at < timedelta(minutes=10):
            return False
        marker.started_at = timezone.now()
        marker.error = ""
        marker.save(update_fields=["started_at", "error"])
        return True


def discovery_mapping(connection, repo, project_id):
    """The mapping a discovered repository writes to.

    A repository already mapped (any project) keeps that project — one project
    per repository. An unmapped repository is attached to the discovering
    issue's project automatically: no initial sync is queued (the discovery
    already upserts the found items) and future webhooks ingest live.
    """
    existing = GitHubRepositoryMapping.objects.filter(connection=connection, repository_id=repo.get("id")).first()
    if existing:
        return existing, False
    mapping = GitHubRepositoryMapping.objects.create(
        connection=connection,
        project_id=project_id,
        repository_id=repo.get("id"),
        full_name=repository_name(repo.get("full_name")),
        is_private=repo.get("private") is True,
        is_auto=True,
        sync_status="synced",
        last_synced_at=timezone.now(),
    )
    return mapping, True


def search_issue_mentions(issue_id):
    """Backfill links by searching every authorized repository for the work item key.

    Runs over every active workspace connection — not just repositories mapped
    to the issue's project — so tagging a work item key anywhere in the
    connected GitHub organizations surfaces on the work item. Pull requests
    and commits found beyond the recent-100 initial sync window are upserted
    so the normal linking rules apply.
    """
    from .client import GitHubClient

    issue = Issue.objects.select_related("project").filter(pk=issue_id, project__deleted_at__isnull=True).first()
    if issue is None:
        return
    marker, _ = GitHubMentionSearch.objects.get_or_create(issue=issue)
    try:
        key = f"{issue.project.identifier}-{issue.sequence_id}"
        connections = GitHubConnection.objects.filter(workspace_id=issue.workspace_id, is_active=True).select_related(
            "app"
        )
        for connection in connections:
            client = GitHubClient(connection.host, connection.app)
            for repo in client.repositories(connection):
                repo_id = repo.get("id")
                found = client.search_issues_for_repo(repo.get("full_name"), key, connection.installation_id)
                if isinstance(found, dict) and found.get("total_count"):
                    mapping, _created = discovery_mapping(connection, repo, issue.project_id)
                    for item in found.get("items", [])[:MENTION_SEARCH_PR_FETCH_LIMIT]:
                        number = positive_id(item.get("number"))
                        data = client.pull_request(mapping, number)
                        if isinstance(data, dict) and data.get("number") == number:
                            upsert_pull_request(mapping, data)
                commits = client.search_commits_for_repo(repo.get("full_name"), key, connection.installation_id)
                if isinstance(commits, dict) and commits.get("total_count"):
                    mapping, _created = discovery_mapping(connection, repo, issue.project_id)
                    for item in commits.get("items", []):
                        upsert_commit(mapping, item)
        if marker:
            marker.completed_at = timezone.now()
            marker.error = ""
            marker.save(update_fields=["completed_at", "error"])
    except Exception as error:  # search failures surface on the ticket, not in the queue
        if marker:
            marker.completed_at = timezone.now()
            marker.error = str(error)[:200]
            marker.save(update_fields=["completed_at", "error"])
        raise


def issue_timeline(issue):
    """The development story of a work item, oldest event first.

    Commits are the first sparks; each pull request contributes its opening
    and, when it happened, its merge (or close). The timeline ends when the
    work reaches the repository's default branch.
    """
    events = []
    for link in GitHubCommitIssueLink.objects.filter(
        issue=issue,
        commit__mapping__connection__workspace_id=issue.workspace_id,
    ).select_related("commit__mapping__connection"):
        commit = link.commit
        events.append(
            {
                "kind": "commit",
                "at": commit.committed_at or commit.created_at,
                "title": (commit.message or "").splitlines()[0] if commit.message else commit.sha[:7],
                "detail": commit.sha[:7],
                "author": commit.author_login or commit.author_name,
                "url": f"{web_base(commit.mapping.connection.host)}/{commit.mapping.full_name}/commit/{commit.sha}",
                "repository": commit.mapping.full_name,
            }
        )
    for link in (
        GitHubIssueLink.objects.filter(
            issue=issue,
            is_suppressed=False,
            pull_request__mapping__connection__workspace_id=issue.workspace_id,
        )
        .select_related("pull_request__mapping__connection")
        .order_by("-pull_request__updated_at")
    ):
        pr = link.pull_request
        opened = pr.remote_created_at or pr.remote_updated_at
        if opened:
            events.append(
                {
                    "kind": "pr_opened",
                    "at": opened,
                    "title": pr.title or f"Pull request #{pr.number}",
                    "detail": f"#{pr.number}",
                    "author": "",
                    "url": pull_request_data(pr)["url"],
                    "repository": pr.mapping.full_name,
                }
            )
        if pr.merged_at:
            events.append(
                {
                    "kind": "pr_merged",
                    "at": pr.merged_at,
                    "title": pr.title or f"Pull request #{pr.number}",
                    "detail": f"#{pr.number}",
                    "author": "",
                    "url": pull_request_data(pr)["url"],
                    "repository": pr.mapping.full_name,
                }
            )
        elif pr.remote_closed_at:
            events.append(
                {
                    "kind": "pr_closed",
                    "at": pr.remote_closed_at,
                    "title": pr.title or f"Pull request #{pr.number}",
                    "detail": f"#{pr.number}",
                    "author": "",
                    "url": pull_request_data(pr)["url"],
                    "repository": pr.mapping.full_name,
                }
            )
    events.sort(key=lambda event: event["at"] or timezone.now())
    return events


def list_issue_development(issue):
    return {
        "pull_requests": list_issue_pull_requests(issue),
        "commits": list_issue_commits(issue),
        "timeline": issue_timeline(issue),
        "mention_search": mention_search_state(issue),
    }


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


def project_key_pattern(project):
    return rf"(?<![A-Za-z0-9_]){re.escape(project.identifier)}-(\d+)(?![A-Za-z0-9_])"


def issues_for_keys(project, workspace_id, text, limit=100):
    """Work items of this project whose keys are mentioned in `text`."""
    values = {
        int(match)
        for match in re.findall(project_key_pattern(project), text, re.IGNORECASE)
        if len(match) <= 10
    }
    if not values:
        return set()
    issues = Issue.objects.filter(
        project=project, workspace_id=workspace_id, sequence_id__in=sorted(values)[:limit]
    )
    return set(issues.values_list("id", flat=True))


def reconcile_links(pr):
    project = pr.mapping.project
    issue_ids = issues_for_keys(
        project, pr.mapping.connection.workspace_id, f"{pr.title}\n{pr.body}\n{pr.head_ref}"
    )
    GitHubIssueLink.objects.filter(pull_request=pr, is_manual=False, is_suppressed=False).exclude(
        issue_id__in=issue_ids
    ).delete()
    for issue_id in issue_ids:
        GitHubIssueLink.objects.get_or_create(pull_request=pr, issue_id=issue_id)


def _automate_merged_pull_request(pr):
    """Move linked work items whose development just completed.

    A work item moves only when every one of its non-suppressed pull requests
    (workspace scope) is merged — at least one of them — and its project has
    automation enabled with a target state. The realtime event is published by
    the work-item save signal, exactly like the board automation executor.
    An automation failure must never break pull request ingestion.
    """
    try:
        issue_ids = list(
            GitHubIssueLink.objects.filter(pull_request=pr, is_suppressed=False).values_list("issue_id", flat=True)
        )
        if not issue_ids:
            return
        for issue in Issue.objects.filter(id__in=issue_ids):
            states = list(
                GitHubIssueLink.objects.filter(
                    issue=issue,
                    is_suppressed=False,
                    pull_request__mapping__connection__workspace_id=issue.workspace_id,
                ).values_list("pull_request__state", flat=True)
            )
            if not states or any(state != "merged" for state in states):
                continue
            automation = GitHubProjectAutomation.objects.filter(
                project_id=issue.project_id, enabled=True, target_state_id__isnull=False
            ).first()
            if not automation or issue.state_id == automation.target_state_id:
                continue
            issue.state_id = automation.target_state_id
            issue.save(update_fields=["state", "updated_at"])
    except Exception as error:
        log_exception(error, warning=True)


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
    was_merged = pr.merged_at is not None
    pr.github_id = github_id
    pr.title = text(data.get("title"), 1000)
    pr.body = text(data.get("body"), 20000)
    pr.head_ref = text(data.get("head", {}).get("ref"), 500)
    pr.merged_at = timestamp(data.get("merged_at"))
    pr.remote_created_at = timestamp(data.get("created_at"))
    pr.remote_closed_at = timestamp(data.get("closed_at"))
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
    if pr.merged_at is not None and not was_merged:
        _automate_merged_pull_request(pr)
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
CONTENT_EVENTS = {"pull_request", "pull_request_review", "release", "push"}


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
    elif delivery.event == "push":
        commits = payload.get("commits")
        if isinstance(commits, list):
            for data in commits:
                upsert_commit(mapping, data)


BACKFILL_PULL_PAGES = 10
BACKFILL_COMMIT_PAGES = 20


def pull_request_mentions(pull):
    """The text of a pull request REST payload that work-item keys live in."""
    head = pull.get("head") if isinstance(pull.get("head"), dict) else {}
    return "\n".join(
        part for part in (pull.get("title"), pull.get("body"), head.get("ref")) if isinstance(part, str)
    )


def commit_message(commit):
    inner = commit.get("commit") if isinstance(commit.get("commit"), dict) else {}
    message = commit.get("message") or inner.get("message")
    return message if isinstance(message, str) else ""


def _backfill_repository(connection, client, repo, patterns):
    """Assign one repository to the project it mentions most and ingest its history."""
    repo_id = repo.get("id")
    pulls = client.repository_pulls(
        repo.get("full_name"), connection.installation_id, repo_id, BACKFILL_PULL_PAGES
    )
    commits = client.repository_commits(
        repo.get("full_name"), connection.installation_id, repo_id, BACKFILL_COMMIT_PAGES
    )
    if not isinstance(pulls, list) or not isinstance(commits, list):
        raise ValidationError("GitHub returned invalid repository history.")
    blobs = [pull_request_mentions(pull) for pull in pulls] + [commit_message(commit) for commit in commits]
    # Patterns arrive ordered by project creation, so ties keep the oldest project.
    winner, winner_hits = None, 0
    for project, pattern in patterns:
        hits = sum(len(pattern.findall(blob)) for blob in blobs)
        if hits > winner_hits:
            winner, winner_hits = project, hits
    if winner is None:
        return
    with transaction.atomic():
        mapping, _created = discovery_mapping(connection, repo, winner.id)
        for data in pulls:
            upsert_pull_request(mapping, data)
        for data in commits:
            upsert_commit(mapping, data)
        mapping.sync_status = "synced"
        mapping.sync_error = ""
        mapping.last_synced_at = timezone.now()
        mapping.save(update_fields=["sync_status", "sync_error", "last_synced_at"])


def backfill_workspace(workspace_id):
    """Deep history sweep: attach repositories to the project they talk about.

    Every repository of every active workspace connection is enumerated and
    its recent pull requests and commits scanned for work-item keys of every
    workspace project. A repository lands on the project it mentions most
    (ties keep the oldest project) and its collected history is ingested
    through the normal upserts. One repository failing never aborts the sweep.
    """
    from .client import GitHubClient

    patterns = [
        (project, re.compile(project_key_pattern(project), re.IGNORECASE))
        for project in Project.objects.filter(workspace_id=workspace_id, deleted_at__isnull=True).order_by(
            "created_at", "id"
        )
    ]
    for connection in GitHubConnection.objects.filter(workspace_id=workspace_id, is_active=True).select_related(
        "app"
    ):
        client = GitHubClient(connection.host, connection.app)
        try:
            repositories = client.repositories(connection)
        except Exception as error:
            log_exception(error, warning=True)
            continue
        for repo in repositories:
            try:
                _backfill_repository(connection, client, repo, patterns)
            except Exception as error:
                log_exception(error, warning=True)
                GitHubRepositoryMapping.objects.filter(
                    connection=connection, repository_id=repo.get("id")
                ).update(sync_status="failed", sync_error=str(error)[:200])


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
