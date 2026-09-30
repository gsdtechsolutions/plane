"""Offline runner tests: subprocesses and HTTP are the external boundaries."""

import importlib.util
from pathlib import Path
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import httpx
import pytest

spec = importlib.util.spec_from_file_location("agent_runner", Path(__file__).with_name("agent_runner.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.fixture
def run(monkeypatch):
    monkeypatch.setenv("AGENT_RUNNER_KEY", "offline-test-key")
    runner = module.Runner()
    runner.event = Mock()
    yield runner
    runner.client.close()


@pytest.fixture
def claim():
    run_id = str(uuid4())
    return {
        "id": run_id,
        "issue": {
            "branch": f"agent/dev-1-{run_id[:8]}",
            "issue_display": "DEV-1",
            "issue_name": "Handle $(malicious) text",
            "description_html": "<p>One &amp; two</p><p>Three</p>",
            "instructions": "Do the requested work",
            "state_name": "Todo",
            "assignee_emails": ["member@example.com"],
            "workspace_slug": "delegation",
            "project_id": str(uuid4()),
            "issue_id": str(uuid4()),
            "repo": "gsdtechsolutions/plane",
        },
    }


def stub_commands(monkeypatch, claim, *, changed=False, failure=None, missing_remote=False):
    calls = []

    def execute(args, **kwargs):
        calls.append((args, kwargs))
        if args[0] == "codex":
            assert "One & two\nThree" in args[-1]
            assert "never touch .git" in args[-1]
            kwargs["stdout"].write("agent output\n")
            kwargs["stdout"].flush()
            if failure:
                raise failure
        output = ""
        code = 0
        if "rev-parse" in args and missing_remote:
            code = 1
        if "--porcelain" in args:
            output = " M app.py\n" if changed else ""
        if args[:3] == ["gh", "pr", "create"]:
            output = "https://github.com/gsdtechsolutions/plane/pull/17\n"
        return SimpleNamespace(returncode=code, stdout=output)

    monkeypatch.setattr(module, "command", execute)
    return calls


def test_no_changes_completes_and_cleans_up(run, claim, monkeypatch):
    calls = stub_commands(monkeypatch, claim, missing_remote=True)
    run.work(claim)
    assert [call.args[1] for call in run.event.call_args_list] == ["running", "completed"]
    assert run.event.call_args.kwargs["result_excerpt"] == "Agent made no changes."
    assert not any(args[0] == "gh" for args, _ in calls)
    add = next(args for args, _ in calls if "worktree" in args and "add" in args)
    assert add[-1] == "gsd/stage"
    assert calls[-1][0][-3:] == ["remove", "--force", f"/tmp/gsd-agent-run-{claim['id'][:8]}"]


def test_changes_commit_push_and_open_draft_pr(run, claim, monkeypatch):
    calls = stub_commands(monkeypatch, claim, changed=True)
    monkeypatch.setattr(
        module, "file_tail", lambda path, limit: "summary" if path.name == "LAST-CHANGES.md" else "agent output"
    )
    run.work(claim)
    assert [call.args[1] for call in run.event.call_args_list] == ["running", "pr_opened", "completed"]
    assert run.event.call_args.kwargs["pr_number"] == 17
    assert run.event.call_args.kwargs["result_excerpt"] == "agent output\n\nsummary"
    commit = next(args for args, _ in calls if "commit" in args)
    assert "Co-authored-by: Plane Coding Agent <agent@gsdut.dev>" in commit[-1]
    push_index = next(i for i, (args, _) in enumerate(calls) if "push" in args)
    gh_index = next(i for i, (args, _) in enumerate(calls) if args[0] == "gh")
    assert push_index < gh_index
    gh = calls[gh_index][0]
    assert "--draft" in gh and gh[gh.index("--base") + 1] == "gsd/stage"
    assert claim["issue"]["issue_id"] in gh[-1] and "summary" in gh[-1]


@pytest.mark.parametrize(
    "failure", [subprocess.TimeoutExpired("codex", 10), subprocess.CalledProcessError(1, "codex", output="bad stderr")]
)
def test_codex_failure_reports_and_always_cleans_up(run, claim, monkeypatch, failure):
    calls = stub_commands(monkeypatch, claim, failure=failure)
    run.work(claim)
    assert [call.args[1] for call in run.event.call_args_list] == ["running", "failed"]
    assert len(run.event.call_args.kwargs["error"]) <= 2000
    assert calls[-1][0][-3] == "remove"
    assert not any("push" in args for args, _ in calls)


def test_command_always_has_timeout_and_no_shell(monkeypatch):
    execute = Mock()
    monkeypatch.setattr(module.subprocess, "run", execute)
    module.command(["git", "status"])
    assert execute.call_args.kwargs["timeout"] == 120
    assert "shell" not in execute.call_args.kwargs


def test_event_retries_transport_failure_with_owner(run, monkeypatch):
    monkeypatch.setattr(module.time, "sleep", lambda seconds: None)
    response = httpx.Response(200, request=httpx.Request("POST", "http://localhost/"))
    run.client.post = Mock(side_effect=[httpx.ReadTimeout("retry"), response])
    module.Runner.event(run, "run-id", "running")
    assert run.client.post.call_count == 2
    assert run.client.post.call_args.kwargs["json"] == {"runner_id": run.runner_id, "status": "running"}
