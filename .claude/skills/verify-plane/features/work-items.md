# Work items

A workspace member creates a project, adds a work item to it, and sees that item in the project's list after a reload.

## Sub-features

- `project-create` creates a project with a name and identifier.
- `item-create` creates a work item in that project.
- `item-list` shows the new work item in the project's work item list after a reload.

## How to get to it (user POV)

- Open Projects in the sidebar, choose Add project, and fill in the name and identifier.
- Inside the project, use New work item, enter a title, and save.

## Driving it with curl and T3 preview

Preconditions:

- Signed in as `verify-user@example.com` with a cookie jar, plus a token from `GET /auth/get-csrf-token/`.

- **Create project.** `POST /api/workspaces/verify/projects/` with JSON `{"name": "Verify Project", "identifier": "VER"}` and the `X-CSRFToken` header. It returns 201 with the project `id`.
- **Create item.** `POST /api/workspaces/verify/projects/<id>/issues/` with JSON `{"name": "Verify item"}`. It returns 201 with `sequence_id` 1.
- **See it.** `preview_navigate` to `/verify/projects/<id>/issues/`. `preview_wait_for` with `text: "Verify item"`. Save a screenshot.
- **Stored.** `stack.sh psql "select name from issues where project_id='<id>'"` lists `Verify item`.

## Gotchas

- Project identifiers are unique per workspace. A second run needs a new identifier or `stack.sh down` first.
- The browser route is `/issues/`, but the UI calls them work items.
