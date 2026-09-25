from celery import shared_task
from django.db import transaction
from django.utils import timezone
from django.db.models import Q
from django.utils.html import strip_tags
from .provider import IntelligenceError, generate_text
from .capture import capture_page
from .browser_capture import capture_browser_page


def readable_pages(project, user):
    from plane.db.models import Page

    return (
        Page.objects.filter(
            project_pages__project=project,
            project_pages__deleted_at__isnull=True,
            workspace_id=project.workspace_id,
            archived_at__isnull=True,
        )
        .filter(Q(access=0) | Q(owned_by=user))
        .distinct()
    )


@shared_task(
    name="plane.release_intelligence.review", queue="release_intelligence", soft_time_limit=150, time_limit=165
)
def run_page_review(review_id):
    from plane.db.models import AppConnection, PageReview, ProjectMember, WorkspaceMember

    if not PageReview.objects.filter(pk=review_id, status="queued").update(status="running"):
        return
    job = PageReview.objects.select_related("project", "requested_by").get(pk=review_id)
    evidence = []
    try:
        if (
            not ProjectMember.objects.filter(
                project=job.project, member=job.requested_by, is_active=True, role__gte=15
            ).exists()
            or not WorkspaceMember.objects.filter(
                workspace_id=job.project.workspace_id, member=job.requested_by, is_active=True
            ).exists()
        ):
            raise IntelligenceError("Your access to this project changed before review started.")
        ids = job.request.get("page_ids", [])
        pages = list(readable_pages(job.project, job.requested_by).filter(id__in=ids))
        if len(pages) != len(ids):
            raise IntelligenceError("A selected documentation page is unavailable or no longer accessible.")
        for page in pages:
            evidence.append(
                {
                    "id": str(page.id),
                    "title": page.name,
                    "kind": "project_page",
                    "url": f"/{job.project.workspace.slug}/projects/{job.project_id}/pages/{page.id}/",
                    "revision": page.updated_at.isoformat(),
                    "captured_at": timezone.now().isoformat(),
                    "content": strip_tags(page.description_html or "")[:10000],
                }
            )
        paths = job.request.get("paths", [])
        if paths:
            connection = AppConnection.objects.filter(project=job.project, enabled=True).first()
            if not connection:
                raise IntelligenceError("Configure and enable the connected app before reviewing its pages.")
            if connection.origin != job.request.get("origin"):
                raise IntelligenceError("The connected origin changed. Start a new review for the updated connection.")
            capture = capture_browser_page if job.request.get("capture_mode") == "browser_rendered" else capture_page
            for path in paths:
                evidence.append(capture(connection.origin, path, connection.current_version))
                PageReview.objects.filter(pk=job.pk).update(evidence=evidence)
        if paths and not any(
            e.get("kind") == "app_page" and len(e.get("content", "").strip()) >= 100 for e in evidence
        ):
            raise IntelligenceError(
                "App content is insufficient for review. Use browser capture for JavaScript pages or select a publicly readable page."
            )
        result = generate_text(job.project, evidence, job.request.get("instructions", ""), review=True)
        PageReview.objects.filter(pk=job.pk).update(
            status="completed", evidence=evidence, results=result, finished_at=timezone.now()
        )
    except Exception as exc:
        message = (
            str(exc)
            if isinstance(exc, IntelligenceError)
            else "The review could not finish. Retry or contact your instance administrator."
        )
        PageReview.objects.filter(pk=job.pk).update(
            status="failed", error=message[:500], evidence=evidence, finished_at=timezone.now()
        )
