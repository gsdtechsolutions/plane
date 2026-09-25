from unittest.mock import patch
import pytest
from rest_framework.test import APIClient
from plane.db.models import DeployBoard, Intake, IntakeIssue, Issue, Project, ProjectMember, State, User


@pytest.fixture
def feedback_board(db, workspace, create_user):
    project = Project.objects.create(name="Feedback", identifier="FDB", workspace=workspace, created_by=create_user)
    ProjectMember.objects.create(project=project, workspace=workspace, member=create_user, role=20)
    state = State.objects.create(project=project, workspace=workspace, name="Todo", group="unstarted", default=True)
    intake = Intake.objects.create(project=project, workspace=workspace, name="Feedback", is_default=True)
    board = DeployBoard.objects.create(
        project=project,
        workspace=workspace,
        entity_name="project",
        entity_identifier=project.id,
        intake=intake,
        is_comments_enabled=True,
        is_votes_enabled=True,
    )
    reporter = User.objects.create(email="public-reporter@example.test", username="public-reporter")
    other = User.objects.create(email="other-reporter@example.test", username="other-reporter")
    return board, state, reporter, other, create_user


def url(board):
    return f"/api/public/anchor/{board.anchor}/intakes/{board.intake_id}/intake-issues/"


def client(user=None):
    result = APIClient()
    if user:
        result.force_authenticate(user)
    return result


def submit(board, user, **extra):
    with patch("plane.space.views.intake.issue_activity.delay"):
        return client(user).post(
            url(board),
            {
                "feedback_type": "bug",
                "issue": {"name": "Broken button", "description_html": "<p>Steps to reproduce</p>"},
                **extra,
            },
            format="json",
        )


@pytest.mark.django_db
def test_feedback_is_private_and_sanitized(feedback_board):
    board, state, reporter, other, admin = feedback_board
    assert submit(board, None).status_code in (401, 403)
    response = submit(
        board, reporter, issue={"name": "Broken button", "description_html": "<script>alert(1)</script><p>Details</p>"}
    )
    assert response.status_code == 201, response.data
    record = IntakeIssue.objects.get(pk=response.data["id"])
    assert record.status == -2 and record.created_by_id == reporter.id
    assert record.feedback_type == "bug" and record.issue.state.is_triage
    assert "<script" not in record.issue.description_html
    assert "created_by" not in response.data and "assignee_details" not in response.data
    detail = url(board) + str(record.id) + "/"
    assert client(other).get(detail).status_code == 404
    assert client(other).get(url(board)).data == []
    assert client(reporter).get(detail).status_code == 200
    assert client(admin).get(detail).status_code == 200


@pytest.mark.django_db
def test_rejects_workflow_injection_and_wrong_intake(feedback_board):
    board, state, reporter, _, _ = feedback_board
    assert submit(board, reporter, issue={"name": "Bad", "state": str(state.id)}).status_code == 400
    assert submit(board, reporter, feedback_type="secret").status_code == 400
    second = Intake.objects.create(project=board.project, workspace=board.workspace, name="Other")
    wrong = url(board).replace(str(board.intake_id), str(second.id))
    assert (
        client(reporter).post(wrong, {"feedback_type": "bug", "issue": {"name": "Bad"}}, format="json").status_code
        == 404
    )
    board.is_disabled = True
    board.save()
    assert submit(board, reporter).status_code == 404


@pytest.mark.django_db
@pytest.mark.parametrize("status", [-2, -1, 0, 2, 1])
def test_only_accepted_feedback_is_public(feedback_board, status):
    board, state, reporter, _, _ = feedback_board
    issue = Issue.objects.create(
        name="Report", project=board.project, workspace=board.workspace, state=state, created_by=reporter
    )
    IntakeIssue.objects.create(
        issue=issue,
        project=board.project,
        workspace=board.workspace,
        intake=board.intake,
        status=status,
        created_by=reporter,
    )
    response = client().get(f"/api/public/anchor/{board.anchor}/issues/{issue.id}/")
    assert response.status_code == (200 if status == 1 else 404)
    if status == 1:
        assert response.data["name"] == "Report"


@pytest.mark.django_db
def test_staff_can_enable_feedback_and_reporter_cannot_edit_accepted(feedback_board):
    board, state, reporter, _, admin = feedback_board
    # Exercise the publish serializer with its real board/project relationship.
    from plane.app.serializers.project import DeployBoardSerializer

    serializer = DeployBoardSerializer(board, data={"submissions_enabled": False}, partial=True)
    assert serializer.is_valid(), serializer.errors
    serializer.save()
    board.refresh_from_db()
    assert board.intake_id is None
    serializer = DeployBoardSerializer(board, data={"submissions_enabled": True}, partial=True)
    assert serializer.is_valid(), serializer.errors
    serializer.save()
    board.refresh_from_db()
    response = submit(board, reporter)
    assert response.status_code == 201, response.data
    record = IntakeIssue.objects.get(pk=response.data["id"])
    record.status = 1
    record.save(disable_auto_set_user=True)
    detail = url(board) + str(record.id) + "/"
    assert client(reporter).patch(detail, {"issue": {"name": "Changed"}}, format="json").status_code == 403
    assert client(reporter).delete(detail).status_code == 403


@pytest.mark.django_db
@pytest.mark.parametrize("grouped", [False, True])
def test_public_list_hides_private_feedback_and_comments(feedback_board, grouped):
    board, state, reporter, _, _ = feedback_board
    from plane.db.models import IssueComment

    public = Issue.objects.create(name="Approved", project=board.project, workspace=board.workspace, state=state)
    hidden = Issue.objects.create(name="Private title", project=board.project, workspace=board.workspace, state=state)
    IntakeIssue.objects.create(
        issue=public, project=board.project, workspace=board.workspace, intake=board.intake, status=1
    )
    IntakeIssue.objects.create(
        issue=hidden, project=board.project, workspace=board.workspace, intake=board.intake, status=-1
    )
    IssueComment.objects.create(
        issue=hidden,
        project=board.project,
        workspace=board.workspace,
        actor=reporter,
        comment_html="<p>Private comment</p>",
        access="EXTERNAL",
    )
    params = {"group_by": "state_id"} if grouped else {}
    response = client().get(f"/api/public/anchor/{board.anchor}/issues/", params)
    assert response.status_code == 200, response.data
    assert "Approved" in str(response.data)
    assert "Private title" not in str(response.data)
    assert client().get(f"/api/public/anchor/{board.anchor}/issues/{hidden.id}/comments/").status_code == 404
    assert (
        client(reporter)
        .post(f"/api/public/anchor/{board.anchor}/issues/{hidden.id}/votes/", {}, format="json")
        .status_code
        == 404
    )


@pytest.mark.django_db
def test_publish_setting_api_and_disabled_public_settings(feedback_board):
    board, _, reporter, _, admin = feedback_board
    settings = f"/api/workspaces/{board.workspace.slug}/projects/{board.project_id}/project-deploy-boards/{board.id}/"
    response = client(admin).patch(settings, {"submissions_enabled": False}, format="json")
    assert response.status_code == 200, response.data
    assert response.data["intake"] is None
    assert client(reporter).patch(settings, {"submissions_enabled": True}, format="json").status_code == 403
    response = client(admin).patch(settings, {"submissions_enabled": True}, format="json")
    assert response.status_code == 200 and response.data["intake"]
    public_settings = f"/api/public/anchor/{board.anchor}/settings/"
    response = client().get(public_settings)
    assert response.status_code == 200
    assert response.data["submissions_enabled"]
    assert "created_by" not in response.data and "updated_by" not in response.data
    board.is_disabled = True
    board.save()
    assert client().get(public_settings).status_code == 404


@pytest.mark.django_db
def test_feedback_submission_limit(feedback_board):
    board, _, reporter, _, _ = feedback_board
    from django.core.cache import cache

    cache.clear()
    with patch("plane.space.views.intake.FeedbackWriteThrottle.rate", "2/hour"):
        assert submit(board, reporter).status_code == 201
        assert submit(board, reporter).status_code == 201
        assert submit(board, reporter).status_code == 429


@pytest.mark.django_db
def test_enabling_feedback_preserves_the_boards_configured_intake(feedback_board):
    board, _, _, _, _ = feedback_board
    from plane.app.serializers.project import DeployBoardSerializer

    custom = Intake.objects.create(project=board.project, workspace=board.workspace, name="Existing custom intake")
    board.intake = custom
    board.save()
    serializer = DeployBoardSerializer(board, data={"submissions_enabled": True}, partial=True)
    assert serializer.is_valid(), serializer.errors
    serializer.save()
    board.refresh_from_db()
    assert board.intake_id == custom.id


@pytest.mark.django_db
def test_description_only_patch_preserves_title(feedback_board):
    board, _, reporter, _, _ = feedback_board
    response = submit(board, reporter)
    assert response.status_code == 201
    detail = url(board) + str(response.data["id"]) + "/"
    changed = client(reporter).patch(detail, {"issue": {"description_html": "<p>Updated details</p>"}}, format="json")
    assert changed.status_code == 200, changed.data
    assert changed.data["name"] == "Broken button"
    assert changed.data["description_html"] == "<p>Updated details</p>"
    title = client(reporter).patch(detail, {"issue": {"name": "Updated title"}}, format="json")
    assert title.status_code == 200, title.data
    assert title.data["description_html"] == "<p>Updated details</p>"


@pytest.mark.django_db
def test_multiple_accepted_intakes_do_not_duplicate_public_issue_or_counts(feedback_board):
    from plane.space.utils.visibility import public_issues

    board, state, _, _, _ = feedback_board
    issue = Issue.objects.create(name="Approved once", project=board.project, workspace=board.workspace, state=state)
    for _ in range(2):
        IntakeIssue.objects.create(
            issue=issue, project=board.project, workspace=board.workspace, intake=board.intake, status=1
        )
    assert public_issues(board).filter(id=issue.id).count() == 1
    for params in ({}, {"group_by": "state_id"}, {"group_by": "state_id", "sub_group_by": "priority"}):
        response = client().get(f"/api/public/anchor/{board.anchor}/issues/", params)
        assert response.status_code == 200, response.data
        assert response.data["total_results"] == 1, response.data
        assert str(response.data["results"]).count("Approved once") == 1


@pytest.mark.django_db
@pytest.mark.parametrize("private_status", [-2, -1, 0, 2])
def test_active_private_intake_blocks_mixed_accepted_issue(feedback_board, private_status):
    from plane.space.utils.visibility import public_issues

    board, state, _, _, _ = feedback_board
    issue = Issue.objects.create(name="Still private", project=board.project, workspace=board.workspace, state=state)
    for item_status in (1, private_status):
        IntakeIssue.objects.create(
            issue=issue, project=board.project, workspace=board.workspace, intake=board.intake, status=item_status
        )
    assert not public_issues(board).filter(id=issue.id).exists()
    assert client().get(f"/api/public/anchor/{board.anchor}/issues/{issue.id}/").status_code == 404


@pytest.mark.django_db
@pytest.mark.parametrize("historical_status", [-2, -1, 1])
def test_deleted_intake_alone_never_turns_report_into_normal_public_issue(feedback_board, historical_status):
    from django.utils import timezone
    from plane.space.utils.visibility import public_issues

    board, state, _, _, _ = feedback_board
    issue = Issue.objects.create(
        name="Retired submission", project=board.project, workspace=board.workspace, state=state
    )
    record = IntakeIssue.objects.create(
        issue=issue, project=board.project, workspace=board.workspace, intake=board.intake, status=historical_status
    )
    IntakeIssue.objects.filter(pk=record.pk).update(deleted_at=timezone.now())
    assert not public_issues(board).filter(id=issue.id).exists()
    assert client().get(f"/api/public/anchor/{board.anchor}/issues/{issue.id}/").status_code == 404
    # A later explicit, active acceptance can publish the same ticket.
    IntakeIssue.objects.create(
        issue=issue, project=board.project, workspace=board.workspace, intake=board.intake, status=1
    )
    assert public_issues(board).filter(id=issue.id).count() == 1
