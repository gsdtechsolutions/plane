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
    b = DeployBoard.objects.create(workspace=workspace, entity_identifier=p.id, entity_name="project")
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
    assert session_client.post(f"{endpoint(p)}{rid}/publish/").status_code == 200
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
    member.role = 15
    member.save()
    created = session_client.post(endpoint(p), {"name": "Draft", "version": "v1"}, format="json")
    assert created.status_code == 201
    assert session_client.post(f"{endpoint(p)}{created.data['id']}/publish/").status_code == 403


def test_publish_requires_notes_and_unique_version(session_client, release_board):
    p, _, _ = release_board
    response = session_client.post(endpoint(p), {"name": "Empty", "version": "v1"}, format="json")
    assert response.status_code == 201
    assert session_client.post(f"{endpoint(p)}{response.data['id']}/publish/").status_code == 400
    assert session_client.post(endpoint(p), {"name": "Again", "version": "v1"}, format="json").status_code == 400
