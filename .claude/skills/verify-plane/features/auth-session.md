# Auth session

A user signs in with email and password, stays signed in across page reloads and days of use, and is signed out only by signing out or by 30 days without activity.

## Sub-features

- `session-signin` posts the sign-in form and lands on the workspace.
- `session-reload` reloads a workspace page and stays signed in.
- `session-lifetime` gives the sign-in cookie `Max-Age` 2592000.
- `session-sliding` re-issues the cookie on every authenticated request and moves the DB `sessions.expire_date` forward.
- `session-signout` ends the session: the row is deleted and `/api/users/me/` returns 401.

## How to get to it (user POV)

- Open `/`, enter the email, continue, enter the password, and submit.
- Reload any page under `/verify/` while signed in.
- Choose Sign out in the workspace sidebar user menu.

## Driving it with stack.sh, curl, Playwright and T3 preview

Preconditions:

- `stack.sh doctor` prints `DOCTOR OK` and `SESSION_COOKIE_AGE 2592000 SESSION_SAVE_EVERY_REQUEST True`.
- `stack.sh seed` has run.

- **HTTP journey.** Run `bin/session-journey.sh`. It signs in, checks the cookie `Max-Age`, waits 3 seconds, calls `GET /api/users/me/`, checks that the cookie was re-issued and the DB expiry moved, then signs out and checks the 401. The last lines read `PASS` and the evidence path.
- **Browser journey, scripted.** Run `node bin/browser-session.mjs`. It signs in through the UI and checks the browser's `session-id` cookie expires in about 30 days. It then reloads `/verify/` and `/verify/projects/` and checks both stay signed in. Screenshots and `transcript.txt` go to the evidence directory, and the last line reads `PASS`.
- **Browser sign-in, interactive.** Use `preview_open` on `http://localhost:38080/`. Fill `#email` with `verify-user@example.com` and click `button[type=submit]`. Fill `#password` with `Verify-Plane-2026!` and click `form:has(#password) button[type=submit]`. Then `preview_wait_for` with `urlIncludes: "/verify/"`.
- **Browser reload.** Run `preview_evaluate` with `location.reload()`, then `preview_wait_for` with `urlIncludes: "/verify/"`. Repeat on `/verify/projects/`. Save a screenshot with `preview_snapshot` and `save: true`. The sidebar and user avatar show, not the sign-in form.
- **DB view.** Run `stack.sh psql "select count(*) from sessions s join users u on u.id=s.user_id where u.email='verify-user@example.com' and s.expire_date > now() + interval '29 days'"`. A count of at least 1 proves the browser session got the 30-day lifetime.

## Gotchas

- `stack.sh up` without a rebuild runs the images of the last build. Check doctor's "images built from" line.
- The local stack is plain HTTP, so cookies are not `Secure`. Production cookies are. Lifetime and sliding behave the same.
- Paths containing `instances` use the `admin-session-id` cookie, not `session-id`. Do not assert user-session renewal on `/api/instances/`.
- `session-journey.sh` signs out at the end. Run the browser steps in their own session.
- A URL under `/verify/` is not proof of being signed in, because a 429 or 502 error page keeps the URL. `browser-session.mjs` requires document status 200 and the sidebar's `a[href^="/verify/projects"]` link.
- Production's web Caddy keeps the 300-per-minute static limit per client IP. A cold workspace load makes about 400 requests, which only works in production because Cloudflare caches the assets. After a deploy changes asset hashes, the first loads are uncached.
