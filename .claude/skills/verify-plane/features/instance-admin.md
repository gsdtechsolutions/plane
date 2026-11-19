# Instance admin

An instance admin signs in at `/god-mode/` to manage instance settings. The admin session is separate from the workspace session and lasts one hour (`ADMIN_SESSION_COOKIE_AGE`, default 3600).

## Sub-features

- `admin-signin` signs in at `/god-mode/` and lands on `/god-mode/general/`.
- `admin-cookie` sets `admin-session-id` with `Max-Age` 3600, separate from `session-id`.
- `admin-guard` sends a non-admin who opens `/god-mode/` back to the admin sign-in.

## How to get to it (user POV)

- Open `/god-mode/` and sign in with the instance admin email and password.

## Driving it with curl and T3 preview

Preconditions:

- `stack.sh seed` has run, which creates the admin `verify-admin@example.com`.

- **HTTP sign-in.** Get a token from `GET /auth/get-csrf-token/`. Post `email`, `password` and `csrfmiddlewaretoken` to `POST /api/instances/admins/sign-in/` with `-D headers.txt`. The response redirects to `/god-mode/general/`, and `headers.txt` sets `admin-session-id` with `Max-Age=3600`.
- **Admin data.** Call `GET /api/instances/admins/me/` with the jar. It returns 200 with the admin's email.
- **Browser.** Use `preview_open` on `http://localhost:38080/god-mode/`. Fill the email and password fields and submit. Then `preview_wait_for` with `urlIncludes: "/god-mode/general"`. Save a screenshot.

## Gotchas

- `SESSION_SAVE_EVERY_REQUEST` also renews the admin cookie on each `/api/instances/` request, with the admin max age.
- The admin sign-up endpoint works only once per instance. `seed` uses it, so later runs must sign in instead.
