from unittest.mock import patch
import pytest
from plane.db.models import Project, ProjectMember, State, Issue, DeployBoard, ProjectRelease

pytestmark = [pytest.mark.contract, pytest.mark.django_db(transaction=True)]


@pytest.fixture(autouse=True)
def external_boundaries():
    with patch("plane.bgtasks.workitem_realtime._schedule_publish"), patch("celery.app.task.Task.apply_async"):
        yield


@pytest.fixture
def release_board(workspace, create_user):
    p = Project.objects.create(name="Release tests", identifier="REL", workspace=workspace)
    ProjectMember.objects.create(project=p, member=create_user, role=20, is_active=True)
    s = State.objects.create(project=p, name="Done", group="completed", color="#777777")
    i = Issue.objects.create(project=p, state=s, name="Private customer title", description_html="<p>Internal</p>")
    b = DeployBoard.objects.create(project=p, workspace=workspace, entity_identifier=p.id, entity_name="project")
    return p, i, b


def endpoint(p):
    return f"/api/workspaces/{p.workspace.slug}/projects/{p.id}/releases/"


def test_draft_publication_and_disabled_board(session_client, release_board):
    p, issue, board = release_board
    response = session_client.post(
        endpoint(p),
        {"name": "Better search", "version": "v1.0", "notes": "Search is faster.", "issue_ids": [str(issue.id)]},
        format="json",
    )
    assert response.status_code == 201, response.data
    rid = response.data["id"]
    from rest_framework.test import APIClient

    public = APIClient()
    feed = f"/api/public/anchor/{board.anchor}/releases/"
    assert public.get(feed).data["releases"] == []
    assert publish(session_client, p, rid).status_code == 200
    result = public.get(feed)
    assert result.status_code == 200
    entry = result.data["releases"][0]
    assert set(entry) == {"id", "name", "version", "notes", "published_at", "app_version"}
    assert "Private customer" not in str(result.data)
    assert entry["notes"] == "Search is faster."
    assert session_client.patch(f"{endpoint(p)}{rid}/", {"notes": "Unreviewed edit"}, format="json").status_code == 400
    assert session_client.post(f"{endpoint(p)}{rid}/unpublish/").status_code == 200
    assert public.get(feed).data["releases"] == []
    board.is_disabled = True
    board.save()
    assert public.get(feed).status_code == 404


def test_release_scope_and_write_roles(session_client, release_board, workspace, create_user):
    p, issue, _ = release_board
    other = Project.objects.create(name="Other", identifier="OTHER", workspace=workspace)
    foreign = Issue.objects.create(project=other, name="Foreign")
    assert (
        session_client.post(
            endpoint(p), {"name": "Bad", "version": "v1", "issue_ids": [str(foreign.id)]}, format="json"
        ).status_code
        == 400
    )
    release = ProjectRelease.objects.create(project=other, name="Hidden", version="v1")
    assert session_client.get(f"{endpoint(p)}{release.id}/").status_code == 404
    member = ProjectMember.objects.get(project=p, member=create_user)
    member.role = 5
    member.save()
    assert session_client.post(endpoint(p), {"name": "Guest", "version": "v1"}, format="json").status_code == 403
    assert session_client.get(endpoint(p)).status_code == 403
    assert session_client.get(endpoint(p) + "options/").status_code == 403
    member.role = 15
    member.save()
    created = session_client.post(endpoint(p), {"name": "Draft", "version": "v1"}, format="json")
    assert created.status_code == 201
    assert publish(session_client, p, created.data["id"]).status_code == 403


def test_publish_requires_notes_and_unique_version(session_client, release_board):
    p, _, _ = release_board
    response = session_client.post(endpoint(p), {"name": "Empty", "version": "v1"}, format="json")
    assert response.status_code == 201
    assert publish(session_client, p, response.data["id"]).status_code == 400
    assert session_client.post(endpoint(p), {"name": "Again", "version": "v1"}, format="json").status_code == 400


def test_generation_is_draft_only_and_preserves_edits_on_failure(session_client, release_board):
    from plane.app.release_intelligence.services import IntelligenceError

    p, issue, _ = release_board
    response = session_client.post(
        endpoint(p),
        {"name": "AI draft", "version": "v2", "notes": "Original", "issue_ids": [str(issue.id)]},
        format="json",
    )
    rid = response.data["id"]
    url = f"{endpoint(p)}{rid}/generate/"
    with patch(
        "plane.app.release_intelligence.services.generate_release_summary",
        side_effect=IntelligenceError("Provider unavailable"),
    ):
        assert session_client.post(url, {}, format="json").status_code == 503
    release = ProjectRelease.objects.get(id=rid)
    assert release.notes == "Original" and release.status == "draft"
    with patch(
        "plane.app.release_intelligence.services.generate_release_summary",
        return_value={
            "text": "Evidence based draft",
            "sources": [{"id": str(issue.id), "title": issue.name}],
            "model": "test",
        },
    ) as generate:
        result = session_client.post(url, {}, format="json")
        assert result.status_code == 200, result.data
        assert generate.call_args.args[1][0]["id"] == str(issue.id)
    release.refresh_from_db()
    assert release.notes == "Evidence based draft" and release.status == "draft" and release.published_at is None

    def concurrent_edit(*args):
        changed = ProjectRelease.objects.get(id=rid)
        changed.notes = "Staff correction"
        changed.save()
        return {"text": "Stale output", "sources": [], "model": "test"}

    with patch("plane.app.release_intelligence.services.generate_release_summary", side_effect=concurrent_edit):
        assert session_client.post(url, {}, format="json").status_code == 409
    release.refresh_from_db()
    assert release.notes == "Staff correction"
    assert publish(session_client, p, rid).status_code == 200
    with patch("plane.app.release_intelligence.services.generate_release_summary") as generate:
        assert session_client.post(url, {}, format="json").status_code == 400
        generate.assert_not_called()


def publish(client, project, release_id):
    release = ProjectRelease.objects.get(id=release_id)
    return client.post(
        f"{endpoint(project)}{release_id}/publish/",
        {"expected_updated_at": release.updated_at.isoformat()},
        format="json",
    )


def test_publish_rejects_unreviewed_replacement(session_client, release_board):
    p, _, _ = release_board
    original = session_client.post(
        endpoint(p), {"name": "Reviewed", "version": "v3", "notes": "Reviewed notes"}, format="json"
    ).data
    changed = session_client.patch(
        f"{endpoint(p)}{original['id']}/", {"notes": "Unreviewed replacement"}, format="json"
    )
    assert changed.status_code == 200
    response = session_client.post(
        f"{endpoint(p)}{original['id']}/publish/", {"expected_updated_at": original["updated_at"]}, format="json"
    )
    assert response.status_code == 409
    release = ProjectRelease.objects.get(id=original["id"])
    assert release.status == "draft" and release.published_at is None


def test_shipped_release_respects_feedback_visibility(session_client, release_board):
    from plane.db.models import Intake, IntakeIssue
    from rest_framework.test import APIClient

    p, issue, board = release_board
    created = session_client.post(
        endpoint(p),
        {"name": "Shipped", "version": "v4", "notes": "Public summary", "issue_ids": [str(issue.id)]},
        format="json",
    )
    assert created.status_code == 201
    assert publish(session_client, p, created.data["id"]).status_code == 200
    public = APIClient()
    url = f"/api/public/anchor/{board.anchor}/issues/{issue.id}/releases/"
    assert len(public.get(url).data["releases"]) == 1
    intake = Intake.objects.create(project=p, name="Feedback")
    submission = IntakeIssue.objects.create(project=p, intake=intake, issue=issue, status=-2)
    assert public.get(url).status_code == 404
    submission.status = 1
    submission.save()
    assert len(public.get(url).data["releases"]) == 1
    board.is_disabled = True
    board.save()
    assert public.get(url).status_code == 404
