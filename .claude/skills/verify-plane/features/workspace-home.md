# Workspace home

After sign-in, a member lands on the home page of their last workspace. Any workspace URL, opened directly or reloaded, renders for a signed-in member and sends a signed-out visitor to sign-in with `next_path`.

## Sub-features

- `home-landing` takes a signed-in user from `/` to `/verify/`.
- `home-deeplink` renders `/verify/projects/` directly for a signed-in member.
- `home-signedout` takes a signed-out visitor from `/verify/projects/` to `/?next_path=%2Fverify%2Fprojects%2F`.

## How to get to it (user POV)

- Sign in at `/`.
- Paste a workspace URL into the address bar.

## Driving it with T3 preview

Preconditions:

- `stack.sh seed` has run, and the browser is signed in as `verify-user@example.com` (see auth-session).

- **Landing.** `preview_navigate` to `http://localhost:38080/`. Then `preview_wait_for` with `urlIncludes: "/verify/"`. The sidebar lists Home, Your work and Projects.
- **Deep link.** `preview_navigate` to `/verify/projects/`, then reload with `preview_evaluate` and `location.reload()`. The URL stays on `/verify/projects/`.
- **Signed out.** In a tab using the `incognito` profile, `preview_navigate` to `/verify/projects/`. The URL becomes `/?next_path=...` and `#email` is visible.

## Gotchas

- A failure in any of the four boot requests (`/api/users/me/`, `profile/`, `settings/`, `workspaces/`) also renders the sign-in screen. When home-landing fails, read `stack.sh logs api` before blaming auth.
- Plane's first load shows a spinner for a few seconds. Wait on the URL or the sidebar, not on a fixed delay.
