# What's New embed (public releases widget)

Embeddable release-notes panel for connected apps, fed by the board's public
releases feed. Everything here is **read-only**: the widget performs a single
credentialess GET per open and never publishes, authenticates the host app, or
mutates data.

## Files
- `apps/web/public/gsd-whats-new.js` — the embeddable script (vanilla JS, no
  dependencies, shadow-root isolated).
- `apps/web/core/components/releases/embed-setup.tsx` — `ReleaseEmbedSetup`
  settings component that generates the copyable install snippet.
- `apps/web/public/gsd-whats-new.test.mjs` +
  `apps/web/core/components/releases/embed-setup.test.mjs` — behaviour tests
  for the pure logic (URL validation, payload normalisation, snippet building).

## Feed contract
`GET <data-feed>` returns (published-only, newest first):

```json
{
  "project_name": "Aether Park",
  "releases": [
    {
      "id": "…",
      "name": "Seat selection",
      "version": "1.2.0",
      "notes": "Plaintext release notes.",
      "published_at": "2026-09-24T12:00:00Z",
      "app_version": "1.2.0"
    }
  ]
}
```

`app_version` may be null. The widget marks the entry matching its
`data-app-version` attribute with a "your version" badge.

## Embedding

```html
<script
  src="https://<board>/gsd-whats-new.js"
  data-feed="https://<board>/api/public/anchor/<anchor>/releases/"
  data-app-version="1.2.0"
  defer
></script>
```

- `data-feed` (required): absolute http(s) URL. Anything else disables the
  widget with a console warning — the widget can never be pointed at a
  relative path, a non-http scheme, or a URL embedding credentials.
- `data-app-version` (optional): the connected app's running version.
- Generating this snippet from inside the board: render `ReleaseEmbedSetup`
  with `{feedUrl, scriptUrl}`.

## Behaviour and safety notes
- The launcher renders bottom-right; nothing auto-opens. One GET fires per
  dialog open with `credentials: "omit"`.
- Feed strings render via `textContent` only — release notes are plaintext by
  contract and never interpreted as HTML.
- Dialog is `role="dialog" aria-modal`, closes on Escape/backdrop, traps Tab
  focus, and restores focus to the launcher.
- Malformed payloads degrade to the empty state; fetch failures show a retry
  hint. The widget logs a console warning and stays inert when misconfigured.
- Publishing stays manual in the board's Releases area; the widget cannot
  create or change releases. Connected-app version reporting requires the
  (not-yet-provided) app integration and is displayed, not authenticated.
