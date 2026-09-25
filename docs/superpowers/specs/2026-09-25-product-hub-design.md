# Connected delivery and feedback — proposed build

## User experience

A customer submits a bug or feature request. The team triages it into a board ticket. That ticket shows its GitHub pull requests and the release containing the change. AI drafts a clear explanation from the linked evidence. The approved changelog appears on the public project page and inside the connected app. Customers can follow progress without seeing internal discussions.

Recommended extra: connect the original request to its shipped release, so requesters can see “Shipped in v…” and the explanation of what changed. This completes the requested workflow before adding unrelated Jira-style administration.

## Build approach

Extend the current board, its existing public Space application, and its Intake workflow. Use a GitHub App for selected repositories. This keeps one project and one set of permissions rather than introducing a second feedback database or a separate project-management product.

Alternatives considered: URL-only PR links would be quick but would not track merge/release changes; adopting a separate feedback service would add another login and synchronization burden. The integrated approach best matches the requested connected app experience.

## 1. GitHub and releases

Workspace admins connect a GitHub App, choose repositories, and map each repository to a board project. Start with read access for repository metadata, pull requests, and releases. The user completes installation and repository authorization on GitHub. Display a clear setup state until the app credentials and callback are configured; never present a simulated connection as live.

Tickets gain a Development section with linked PRs, review/merge state, repository, and release. Recognize project ticket keys in PR titles, descriptions, and branches, with manual link/unlink for exceptions. Only map references within the configured project/repository boundary. GitHub webhook processing verifies the signature, records unique delivery IDs, and queues processing so retries do not duplicate records. Disconnecting removes access and stops synchronization without destroying local ticket history.

A Releases area groups linked tickets and PRs by version/tag and keeps draft notes separate from published notes. A merged PR is not labeled shipped merely because it merged. Release membership is based on the chosen release comparison or explicit staff selection; deployment/version evidence can distinguish released from running in the app.

Reuse findings: GitHub repository/sync models and connection/repository-picker screens exist, but the expected integration API routes are absent from this build. These are scaffolding to complete, not a working connection to advertise.

## 2. AI release summaries and app connection

Each release can generate an editable draft with Features, Fixes, and Changes. Inputs are explicitly linked tickets, PR descriptions, release metadata, and selected project pages. Keep source links beside the draft so claims can be checked. Label insufficient evidence and avoid claiming a feature is deployed without release/deployment evidence.

Page review has two explicit inputs: selected board documentation pages and selected pages of the connected application. Store the reviewed URL/page revision, capture time, app version when available, and findings. A live app review uses an explicitly configured origin and a bounded capture job; authenticated app access is configured separately rather than borrowing a user's board session. Drafts can be generated from repository/ticket evidence before that live-page connection is available.

Use the instance's configured AI provider through a server-side adapter with timeouts, bounded inputs, job status, and readable failure states. Existing AI code has a provider configuration surface but sends requests through one client and restricts models to an old hardcoded list; correct that behavior in the touched path. Provider credentials stay server-side. Do not automatically publish model output.

For the connected app, provide a stable published-changelog JSON endpoint and an embeddable What's New panel. Public feeds expose only explicitly published release notes and public identifiers. Deployment metadata may report the app's current version through a separately scoped integration endpoint. Initial publishing targets the board's own public feed; writing GitHub releases or modifying another app's repository requires its selected target and authorization.

## 3. Public feature requests and bug reports

Extend the existing public Space board with Submit feedback. The form offers Bug report or Feature request, title, and description. Require existing Space sign-in for submissions and votes. New reports enter private Intake moderation; public visitors see only accepted, explicitly public records. Staff can accept, reject, mark duplicate, or link to an existing ticket.

Reuse existing votes, external comments, Intake statuses, and publishing settings. Add the missing public form and staff control for enabling submissions. Bind each request to the board's configured Intake, rather than accepting arbitrary project/state/label fields from public callers.

Before exposing this flow, fix the existing public listing path that includes rejected Intake records. Do not reuse the unfiltered Intake list/retrieve endpoints as a customer “my submissions” view. Separate public fields from internal ticket data and apply public write limits. Never expose internal comments, reporter email addresses, private PR descriptions, or draft release notes through the portal/widget.

## Ownership and order

Base: clean `feat/board-improvements` at `6c81bf2355b920216ea0dc883d618890d1e071de`. Preserve the current validated preview.

- Codex: GitHub connection, ticket/release relationships, release drafts, AI/job adapter, integration and validation.
- Existing ZCode session: public feedback flow and its settings, using a separate worktree after it acknowledges the assignment and shared API contract.
- One coordinator owns migrations, shared route registration, and the authoritative preview; reserve migration numbers before parallel writes.

Deliver in slices: first connection + ticket/PR tracking and public submission/triage in parallel; then release summaries/public feed; then the in-app panel and live app-page review against the chosen application. Each slice gets real browser validation and focused backend tests before integration. No new source work has started during this design pass.

## Acceptance

A selected GitHub repository connects through real authorization; a PR referencing a test ticket appears once and updates on merge; forged/replayed/mismatched webhook deliveries cannot cross project boundaries. A draft release identifies its included tickets and generates an editable, source-linked summary. Publishing exposes only approved fields through the feed/widget. A public report enters private triage, becomes visible only after acceptance, and can be linked to its eventual release. Private records remain inaccessible across workspaces and from public endpoints. The existing board features continue to pass their checks.

## Inputs needed

The first GitHub organization/repository and application URL determine the actual connection and browser tests. GitHub installation is completed by the user when its permission screen is ready. The Mac must be unlocked to resume the existing ZCode app session: fakechat has received our brief, but ZCode has not yet acknowledged the new assignment.

## References

GitHub App installation supports selected repositories: https://docs.github.com/en/apps/creating-github-apps/writing-code-for-a-github-app/quickstart
Webhook signatures must be validated before processing: https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries
