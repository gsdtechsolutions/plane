#!/usr/bin/env python3
# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
"""Poll Plane and run delegated issues. Requires httpx, codex, git and authenticated gh.

Run with the API venv's Python and AGENT_RUNNER_KEY set. Credentials stay in
HTTP headers; issue text is passed as an argument, never evaluated by a shell.
"""

import logging
import os
from pathlib import Path
import re
import socket
import subprocess
import tempfile
import time
from html.parser import HTMLParser
from uuid import UUID, uuid4

import httpx

logger = logging.getLogger("agent_runner")


class DescriptionText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)

    def handle_endtag(self, tag):
        if tag in {"p", "div", "li", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_starttag(self, tag, attrs):
        if tag == "br":
            self.parts.append("\n")


def description_text(html):
    parser = DescriptionText()
    parser.feed(html or "")
    return "".join(parser.parts).strip()


def command(args, *, timeout=120, check=True, stdout=None):
    return subprocess.run(
        args,
        timeout=timeout,
        check=check,
        text=True,
        stdout=stdout if stdout is not None else subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def file_tail(path, limit):
    if not path.exists():
        return ""
    with path.open("rb") as stream:
        stream.seek(0, 2)
        stream.seek(max(0, stream.tell() - limit * 4))
        return stream.read().decode("utf-8", errors="replace")[-limit:]


class Runner:
    def __init__(self):
        key = os.environ.get("AGENT_RUNNER_KEY", "")
        if not key:
            raise ValueError("AGENT_RUNNER_KEY is required")
        self.base = os.environ.get("PLANE_API_BASE", "http://localhost:8000").rstrip("/")
        self.api = self.base if self.base.endswith("/api") else self.base + "/api"
        self.repo = os.environ.get("PLANE_REPO_DIR", "/workspace/gsd-plane/plane")
        self.codex = os.environ.get("CODEX_BIN", "codex")
        self.poll = max(1, int(os.environ.get("POLL_SECONDS", "15")))
        self.timeout = max(1, int(os.environ.get("RUN_TIMEOUT_SECONDS", "3600")))
        self.runner_id = f"{socket.gethostname()[:80]}-{uuid4().hex[:12]}"
        self.client = httpx.Client(headers={"X-Runner-Key": key}, timeout=30)

    def event(self, run_id, status, **fields):
        payload = {"runner_id": self.runner_id, "status": status, **fields}
        for attempt in range(3):
            try:
                response = self.client.post(f"{self.api}/agent-runner/{run_id}/events/", json=payload)
                response.raise_for_status()
                return
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code < 500 or attempt == 2:
                    raise
            except httpx.TransportError:
                if attempt == 2:
                    raise
            time.sleep(attempt + 1)

    def brief(self, issue):
        return (
            f"Implement Plane issue {issue['issue_display']}: {issue['issue_name']}\n\n"
            f"Description:\n{description_text(issue.get('description_html'))}\n\n"
            f"State: {issue.get('state_name', '')}\n"
            f"Assignees: {', '.join(issue.get('assignee_emails', [])) or 'Unassigned'}\n\n"
            f"Instructions:\n{issue.get('instructions', '')}\n\n"
            "Guardrails: work only inside this worktree; never touch .git; "
            "run tests you can run quickly; leave a summary in LAST-CHANGES.md "
            "at worktree root. Do not commit, push, or open a PR yourself; "
            "the runner handles those steps.\n"
        )

    def work(self, claim):
        run_id = str(UUID(claim["id"]))
        issue = claim["issue"]
        branch = issue["branch"]
        worktree = Path(f"/tmp/gsd-agent-run-{run_id[:8]}")
        brief_path = None
        log_path = None
        try:
            self.event(run_id, "running", branch=branch)
            # Validate the API-supplied branch before using it in git arguments.
            if not branch.startswith("agent/"):
                raise ValueError("Invalid agent branch")
            command(["git", "check-ref-format", "--branch", branch])
            try:
                command(["git", "-C", self.repo, "fetch", "origin", "gsd/stage"], timeout=60, check=False)
            except subprocess.TimeoutExpired:
                logger.warning("Fetch timed out; using the existing stage ref")
            ref = command(["git", "-C", self.repo, "rev-parse", "--verify", "origin/gsd/stage"], check=False)
            base = "origin/gsd/stage" if ref.returncode == 0 else "gsd/stage"
            command(["git", "-C", self.repo, "worktree", "add", str(worktree), "-b", branch, base])
            with tempfile.NamedTemporaryFile(
                mode="w", prefix=f"gsd-agent-brief-{run_id[:8]}-", suffix=".txt", delete=False
            ) as brief:
                brief_path = Path(brief.name)
                brief.write(self.brief(issue))
            with tempfile.NamedTemporaryFile(
                mode="w", prefix=f"gsd-agent-output-{run_id[:8]}-", suffix=".log", delete=False
            ) as output:
                log_path = Path(output.name)
                command(
                    [
                        self.codex,
                        "exec",
                        "--sandbox",
                        "workspace-write",
                        "-c",
                        "sandbox_workspace_write.network_access=true",
                        "-C",
                        str(worktree),
                        brief_path.read_text(),
                    ],
                    timeout=self.timeout,
                    stdout=output,
                )
            changed = command(["git", "-C", str(worktree), "status", "--porcelain"]).stdout.strip()
            if not changed:
                self.event(run_id, "completed", result_excerpt="Agent made no changes.")
                return
            summary = file_tail(worktree / "LAST-CHANGES.md", 4000)
            excerpt = (file_tail(log_path, 4000) + ("\n\n" + summary if summary else ""))[:8000]
            command(["git", "-C", str(worktree), "add", "-A"])
            display = issue["issue_display"]
            message = (
                f"agent({display}): delegated run {run_id[:8]}\n\n"
                f"Delegated from Plane issue {display}.\n\n"
                "Co-authored-by: Plane Coding Agent <agent@gsdut.dev>"
            )
            command(
                [
                    "git",
                    "-C",
                    str(worktree),
                    "-c",
                    "user.name=Plane Coding Agent",
                    "-c",
                    "user.email=agent@gsdut.dev",
                    "commit",
                    "-m",
                    message,
                ]
            )
            command(["git", "-C", str(worktree), "push", "origin", branch], timeout=180)
            issue_url = issue.get("issue_url") or (
                f"{self.base.removesuffix('/api')}/{issue['workspace_slug']}/projects/"
                f"{issue['project_id']}/issues/{issue['issue_id']}"
            )
            body = f"{summary or 'Delegated issue implementation.'}\n\nPlane issue: {issue_url}"
            result = command(
                [
                    "gh",
                    "pr",
                    "create",
                    "--repo",
                    issue.get("repo") or "gsdtechsolutions/plane",
                    "--draft",
                    "--base",
                    "gsd/stage",
                    "--head",
                    branch,
                    "--title",
                    f"{display}: {issue['issue_name']}",
                    "--body",
                    body,
                ],
                timeout=180,
            )
            match = re.search(r"https://[^\s]+/pull/(\d+)", result.stdout)
            if not match:
                raise ValueError("gh did not return a PR URL")
            pr = {"pr_url": match.group(0), "pr_number": int(match.group(1)), "branch": branch}
            self.event(run_id, "pr_opened", **pr)
            self.event(run_id, "completed", result_excerpt=excerpt, **pr)
            logger.info("Run %s opened %s; output: %s", run_id, pr["pr_url"], log_path)
        except Exception as exc:
            output = file_tail(log_path, 1600) if log_path else ""
            if isinstance(exc, subprocess.CalledProcessError):
                output = exc.output or output
            error = (f"{type(exc).__name__}: {exc}\n" + output)[-2000:]
            logger.error("Run %s failed (%s); output: %s", run_id, type(exc).__name__, log_path)
            try:
                self.event(run_id, "failed", error=error)
            except Exception:
                logger.exception("Could not report failure for run %s", run_id)
        finally:
            try:
                command(["git", "-C", self.repo, "worktree", "remove", "--force", str(worktree)], check=False)
            except Exception:
                logger.exception("Worktree cleanup failed for run %s", run_id)
            if brief_path:
                try:
                    brief_path.unlink(missing_ok=True)
                except OSError:
                    logger.exception("Brief cleanup failed for run %s", run_id)

    def loop(self):
        logger.info("Runner %s polling %s", self.runner_id, self.api)
        try:
            while True:
                try:
                    response = self.client.post(f"{self.api}/agent-runner/claim/", json={"runner_id": self.runner_id})
                    if response.status_code == 204:
                        time.sleep(self.poll)
                        continue
                    response.raise_for_status()
                    self.work(response.json())
                except Exception:
                    logger.exception("Claim or run failed; polling continues")
                    time.sleep(self.poll)
        finally:
            self.client.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        Runner().loop()
    except KeyboardInterrupt:
        logger.info("Runner stopped")
