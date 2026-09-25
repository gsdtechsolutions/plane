# Release intelligence runtime

Register `include("plane.app.release_intelligence.urls")` under the existing `/api/` URL prefix. Mount `PageReviewPanel` in the project Releases UI. Migration 0147 depends on 0143; merge concurrent migration leaves before release.

The existing Celery worker autodiscovers `plane.app.tasks`, registering `plane.release_intelligence.review`. Start an API-matching worker with `celery -A plane worker -Q release_intelligence --concurrency=1 --loglevel=info`. Jobs use a 150 second soft / 165 second hard deadline and return readable failure or stalled state when workers are unavailable.

AI reads server-only LLM_API_KEY, LLM_PROVIDER, LLM_MODEL, and optional LLM_BASE_URL from instance configuration/environment; OPENAI_API_BASE is a fallback. OpenAI-compatible providers accept arbitrary configured models. Anthropic uses its messages endpoint. Gemini defaults to its OpenAI-compatible endpoint. Do not expose credentials to the UI. Real generation is not validated until a provider is configured.

## Optional browser worker

Use a dedicated unprivileged sandboxed worker with restricted network egress and no application secrets beyond required API/DB/provider access. Install `pip install -r requirements/release-browser.txt`, then `python -m playwright install --with-deps chromium`; set RELEASE_BROWSER_ENABLED=1 on the API (capability display) and capture worker. Chromium sandbox is enabled; do not bypass it to make a privileged container work. Start the dedicated `release_intelligence` queue worker using the same source/migrations as the API. Dispatch explicitly targets this queue so older/default workers cannot consume these jobs.

Every browser request is intercepted and fulfilled through vetted public-IP connections to the configured origin. Chromium's own proxy points to a closed local endpoint, WebSockets and service workers are blocked, and no board cookies or storage are provided. Limits: 60 requests, 8 MB aggregate, 1 MB/resource, 35-second navigation, three paths/job. Resources from other origins are blocked and marked as incomplete evidence. Public pages only. Authenticated apps need a separately scoped credential design, not borrowed board sessions.

Captures retain rendered text, screenshot, revision hash, capture time, configured app version and limitations. AI reviews rendered text, not screenshot pixels. HTTP-only captures are never called browser-verified. Worker startup and browser capture require a real configured origin for end-to-end validation; mocks do not fulfill that gate.

Playwright API reference: https://playwright.dev/python/docs/api/class-browsercontext (routing, service worker blocking and WebSocket interception).
