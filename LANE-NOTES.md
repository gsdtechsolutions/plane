# slack-asks lane

Implemented the message shortcut draft/modal/submission flow and ephemeral workspace Q&A via `/plane-ask` and `/plane ask ...`. The existing signed interactivity endpoint handles both the requested `message_shortcut` alias and Slack's `message_action` payload. Thread drafts include available linked-issue context; submissions validate project choices, attribute creation to the email-matched workspace member or connection owner, escape description HTML, deduplicate creation transactionally, and post a reply in the original thread. Q&A searches titles, descriptions, and comments, includes bounded matching comments as evidence, and links up to eight sources. AI actions and failures use the shared audit service; Slack posting failures preserve the created issue and produce an ignored delivery record.

## Files changed

- `apps/api/plane/app/slack_delivery/asks.py` — new thread draft, modal submission, delivery deduplication, auditing, workspace search and answer helpers.
- `apps/api/plane/app/slack_delivery/api.py` — shortcut/submission routing and `/plane-ask` command acknowledgement.
- `apps/api/plane/app/slack_delivery/client.py` — shortcut/command manifest entries, `chat:write` scope, thread reads, modal open, ephemeral messages, optional thread replies.
- `apps/api/plane/app/slack_delivery/commands.py` — `/plane-ask` and `/plane ask` dispatch through the existing command task.
- `apps/api/plane/tests/contract/app/test_slack_asks.py` — 21 contract test cases.
- `LANE-NOTES.md` — coordinator handoff.

No shared modules or existing tests were edited. No git operations or commits were performed.

## Slack dashboard / manifest updates

For existing apps, apply these definitions in the Slack dashboard (new generated manifests contain them):

1. Interactivity & Shortcuts: add an **On messages** shortcut named `Create issue from thread`, description `Draft a Plane issue from this thread`, callback ID `create_issue_from_thread`. Keep the request URL `<origin>/api/slack-delivery/interactivity/`.
2. Slash Commands: add `/plane-ask`, request URL `<origin>/api/slack-delivery/commands/`, description `Ask AI about this workspace's work`, usage hint `[question]`. Retain `/plane` and its URL. The `/plane ask ...` alias also works.
3. OAuth & Permissions: add bot scope `chat:write`, then reinstall/reconnect existing installations so their stored bot tokens gain it. Private channels still require the bot to be invited.
4. Configure the instance AI API key/provider/model for AI generation.

The shortcut manifest retains the requested `action: message_action` field and also supplies the required `description`. Slack's [documented manifest fields](https://docs.slack.dev/reference/app-manifest/) are type/name/description/callback_id; the actual shortcut interaction type is `message_action`, which this lane accepts alongside `message_shortcut`.

## Shared wiring

None. Existing interactivity and command URLs and the registered `slack_delivery.command` task/queue are reused. No URL, Celery, model export, settings, or migration changes are needed.

## Validation results

Environment loaded using the supplied NUL-delimited `/tmp/api-env-new.env` loop; `SLACK_APP_BASE_URL` exported empty; Python binary `/workspace/gsd-plane/plane/apps/api/.venv/bin/python`.

| Check | Result | Exit |
| --- | --- | --- |
| Exact asks-only pytest command with `--create-db -q` | 21 passed, 12 warnings; 445.24s | 0 |
| Exact full Slack trio command with `-q`, without `--create-db` | 132 passed, 1 failed, 67 warnings; 2532.47s | 1 |
| `python -m compileall -q` over all five changed Python files | Passed | 0 |
| Ruff check of new asks module and contract test file | Passed | 0 |

Logs: `/tmp/asks-pytest.log`, `/tmp/asks-pytest-all.log`. Warnings concern the absent collected static-assets directory.

The sole full-trio failure is `test_setup_view_shape_and_manifest` at `apps/api/plane/tests/contract/app/test_slack_delivery.py:594`. It asserts the slash-command list contains only `/plane`, so the required `/plane-ask` addition fails it. The user's instruction permits edits to this file only for changed ignored-type expectations; this failure is unrelated to ignored types. An asynchronous request to authorize this narrow assertion update received no answer, so the file remains unchanged. All 21 asks tests and all 47 existing command tests passed; 64 of 65 delivery tests passed.

Coordinator correction: append the following second object to the expected `manifest["features"]["slash_commands"]` list in that test, retaining the existing `/plane` object:

```python
{
    "command": "/plane-ask",
    "url": "http://localhost:3002/api/slack-delivery/commands/",
    "description": "Ask AI about this workspace's work",
    "usage_hint": "[question]",
    "should_escape": False,
}
```

## Tradeoffs and integration limits

- Shortcut thread fetch, LLM generation and `views.open` run synchronously, per the specified final decision. Slack's trigger expires in roughly three seconds, so provider/network latency can prevent the modal from opening or provoke Slack retries. Draft retries are deduplicated; expected Slack/provider failures acknowledge with HTTP 200, record the error, and attempt an ephemeral hint. This lane does not claim a three-second guarantee.
- The shared provider accepts a project and a list of evidence objects, reads `project.name`, and retains a release-oriented system prompt. The lane adapts evidence into that contract and supplies explicit draft/Q&A instructions. Its existing 2000-token cap and 25-second provider timeout cannot be lowered through `generate_text` arguments; the provider module was left outside this lane's changes. Live provider output and Slack trigger timing were not exercised by the mocked external boundaries in the contract suite.
- Thread fetch uses one page of up to 100 messages to avoid additional synchronous network hops. Evidence is ordered oldest to newest and bounded to roughly 12k characters; very long threads can be incomplete.
- Project has no issue-module-enabled flag in this fork. With no active mappings, the modal falls back to nondeleted workspace projects; with no mapped project context, Q&A returns source links without calling the project-required provider.
- Modal cancellation uses Slack's documented `notify_on_close: false` field. Submissions require a nonempty view ID for deduplication and cap input lengths to the modal's limits.
- Run database validations sequentially against `test_plane`. `--create-db` prompts when the reused database already exists; the final recreation run used a terminal to answer that prompt.
