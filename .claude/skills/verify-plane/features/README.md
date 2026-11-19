# Plane verification map

This directory is the maintained source for verifying user-facing behavior of the GSD Plane fork. Read this index before driving the app, then use the matching feature file as the recipe.

## Baseline preconditions

- The stack is built from the checkout under test and started with `bin/stack.sh up`. It answers at `http://localhost:38080`.
- `bin/stack.sh doctor` prints `DOCTOR OK`, and the "images built from" commit matches the checkout.
- `bin/stack.sh seed` has run. It creates `verify-admin@example.com` (instance admin), and `verify-user@example.com` as an onboarded admin of workspace `verify`. Both use the password `Verify-Plane-2026!`.
- Never drive production for a pre-merge proof. The only production journey is the post-deploy smoke in `../SKILL.md`.

## Driving conventions

- Start every recipe signed out, in a fresh preview tab or with an empty cookie jar.
- For HTTP, get a CSRF token from `GET /auth/get-csrf-token/` before every form post.
- In the browser, prefer element ids and route paths over text and DOM position. The UI text is translated.
- Run read-only DB checks through `bin/stack.sh psql "<sql>"`.

## Proof and skip reporting

- Capture the action and the resulting state: response headers, the DB row, or a screenshot of the page after the action.
- Store artifacts under `~/.local/state/plane-verify/evidence/<timestamp>-<feature>/`.
- Record the feature ID and entry point with each artifact.
- Report an unreachable path with the attempted step and the unmet precondition. Never report it as verified through a different entry point.

## Feature entry contract

Each feature file starts with an H1 title and one paragraph describing the user-visible behavior. It then has exactly four H2 sections in this order: `Sub-features`, `How to get to it (user POV)`, `Driving it with <harness>`, and `Gotchas`.

## Features

- [Auth session](./auth-session.md) covers sign-in, staying signed in across reloads, session renewal on activity, and sign-out.
- [Instance admin](./instance-admin.md) covers god-mode sign-in and the shorter admin session.
- [Workspace home](./workspace-home.md) covers landing on the workspace after sign-in and deep-link reloads.
- [Work items](./work-items.md) covers creating a project and a work item and seeing it in the list.
