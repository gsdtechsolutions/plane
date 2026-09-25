import { useEffect, useState } from "react";
import useSWR from "swr";
import { ReleaseIntelligenceService } from "@/services/release-intelligence.service";
import type { AppConnection, ReviewRequest } from "@/services/release-intelligence.service";

const service = new ReleaseIntelligenceService();
const field = "w-full rounded-md border border-subtle bg-surface-1 px-3 py-2 text-sm";
const button = "rounded-md bg-accent-primary px-4 py-2 text-sm font-medium text-white disabled:opacity-50";
function readableError(error: unknown): string {
  const value = error as { response?: { data?: { error?: string; detail?: string } } };
  return (
    value?.response?.data?.error ||
    value?.response?.data?.detail ||
    "The request could not finish. Check your access and connection, then retry."
  );
}

export function PageReviewPanel({ workspaceSlug, projectId }: { workspaceSlug: string; projectId: string }) {
  const key = ["page-review", workspaceSlug, projectId];
  const {
    data: connection,
    error: connectionError,
    mutate: refreshConnection,
  } = useSWR([...key, "connection"], () => service.connection(workspaceSlug, projectId));
  const { data: pages, error: pagesError } = useSWR([...key, "pages"], () => service.pages(workspaceSlug, projectId));
  const { data: capabilities } = useSWR([...key, "capabilities"], () => service.capabilities(workspaceSlug, projectId));
  const {
    data: reviews,
    error: reviewsError,
    mutate: refreshReviews,
  } = useSWR([...key, "jobs"], () => service.reviews(workspaceSlug, projectId), {
    refreshInterval: (data) => (data?.some((job) => ["queued", "running"].includes(job.status)) ? 2500 : 0),
  });
  const [app, setApp] = useState<AppConnection>({ origin: "", current_version: "", enabled: true });
  const [selected, setSelected] = useState<string[]>([]);
  const [paths, setPaths] = useState("");
  const [instructions, setInstructions] = useState("");
  const [mode, setMode] = useState<ReviewRequest["capture_mode"]>("http_only");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    if (connection) setApp(connection);
  }, [connection]);

  async function saveConnection(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await service.saveConnection(workspaceSlug, projectId, app);
      await refreshConnection();
      setMessage("App connection saved.");
    } catch (failure) {
      setError(readableError(failure));
    } finally {
      setBusy(false);
    }
  }
  async function startReview(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await service.create(workspaceSlug, projectId, {
        page_ids: selected,
        paths: paths
          .split("\n")
          .map((path) => path.trim())
          .filter(Boolean),
        instructions,
        capture_mode: mode,
      });
      await refreshReviews();
      setMessage("Review queued. Results appear below when capture and analysis finish.");
    } catch (failure) {
      setError(readableError(failure));
    } finally {
      setBusy(false);
    }
  }
  const loadError = connectionError || pagesError || reviewsError;
  return (
    <section aria-labelledby="page-review-title" className="space-y-6 rounded-lg border border-subtle p-5">
      <div>
        <h2 id="page-review-title" className="text-lg font-semibold">
          Review your app against its documentation
        </h2>
        <p className="text-sm mt-1 text-secondary">
          Choose project pages and public app paths. Review drafts stay private to you and are never published
          automatically.
        </p>
      </div>
      {(error || loadError) && (
        <p role="alert" className="text-sm text-red-600">
          {error || readableError(loadError)}
        </p>
      )}
      {message && (
        <p role="status" className="text-sm">
          {message}
        </p>
      )}
      {capabilities && !capabilities.ai_configured && (
        <p role="status" className="text-sm rounded-md bg-layer-2 p-3">
          {capabilities.ai_error}
        </p>
      )}
      <details className="rounded-md border border-subtle p-4" open={!connection}>
        <summary className="cursor-pointer font-medium">
          Connected app {connection?.origin ? `— ${connection.origin}` : "— not configured"}
        </summary>
        <form onSubmit={saveConnection} className="mt-4 grid gap-3">
          <label className="text-sm">
            App origin
            <input
              className={field}
              type="url"
              required
              placeholder="https://app.example.com"
              value={app.origin}
              onChange={(event) => setApp({ ...app, origin: event.target.value })}
            />
          </label>
          <label className="text-sm">
            Current app version <span className="text-secondary">(optional)</span>
            <input
              className={field}
              maxLength={120}
              value={app.current_version}
              onChange={(event) => setApp({ ...app, current_version: event.target.value })}
            />
          </label>
          <label className="text-sm flex items-center gap-2">
            <input
              type="checkbox"
              checked={app.enabled}
              onChange={(event) => setApp({ ...app, enabled: event.target.checked })}
            />
            Enable app capture
          </label>
          <p className="text-xs text-secondary">
            Project administrators configure the origin. Public pages only; board login credentials are never sent to
            the app.
          </p>
          <div>
            <button type="submit" className={button} disabled={busy}>
              Save connection
            </button>
          </div>
        </form>
      </details>
      <form onSubmit={startReview} className="grid gap-4">
        <fieldset>
          <legend className="text-sm mb-2 font-medium">
            Documentation <span className="text-secondary">(up to 5 pages)</span>
          </legend>
          {pages === undefined ? (
            <p className="text-sm text-secondary">Loading documentation…</p>
          ) : pages.length === 0 ? (
            <p className="text-sm text-secondary">
              No accessible project pages yet. You can still review public app pages.
            </p>
          ) : (
            <div className="max-h-48 space-y-2 overflow-auto">
              {pages.map((page) => (
                <label key={page.id} className="text-sm flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={selected.includes(page.id)}
                    disabled={!selected.includes(page.id) && selected.length >= 5}
                    onChange={(event) =>
                      setSelected(
                        event.target.checked ? [...selected, page.id] : selected.filter((id) => id !== page.id)
                      )
                    }
                  />
                  {page.name || "Untitled page"}
                </label>
              ))}
            </div>
          )}
        </fieldset>
        <label className="text-sm font-medium">
          App paths <span className="font-normal text-secondary">(one per line, up to 3)</span>
          <textarea
            className={field}
            rows={3}
            placeholder={"/\n/settings"}
            value={paths}
            onChange={(event) => setPaths(event.target.value)}
          />
        </label>
        <label className="text-sm font-medium">
          Capture method
          <select
            className={field}
            value={mode}
            onChange={(event) => setMode(event.target.value as ReviewRequest["capture_mode"])}
          >
            <option value="http_only">HTTP text — no JavaScript rendering</option>
            <option value="browser_rendered" disabled={!capabilities?.browser_available}>
              Browser rendering — JavaScript and screenshot
            </option>
          </select>
        </label>
        <p className="text-xs text-secondary">
          {mode === "http_only"
            ? "HTTP capture cannot verify JavaScript-rendered content, appearance or interaction. Choose browser rendering for JavaScript apps."
            : "Browser capture records public rendered text and a screenshot. Analysis uses text; authenticated pages and interaction flows are not tested."}{" "}
          {capabilities && !capabilities.browser_available && capabilities.browser_message}
        </p>
        <label className="text-sm font-medium">
          Review focus <span className="font-normal text-secondary">(optional)</span>
          <textarea
            className={field}
            rows={2}
            maxLength={2000}
            placeholder="Check whether the documented onboarding steps match the app."
            value={instructions}
            onChange={(event) => setInstructions(event.target.value)}
          />
        </label>
        <div>
          <button
            className={button}
            type="submit"
            disabled={busy || !capabilities?.ai_configured || (!selected.length && !paths.trim())}
          >
            {busy ? "Working…" : "Start review"}
          </button>
        </div>
      </form>
      <div className="space-y-4" aria-live="polite">
        {reviews?.map((job) => (
          <article key={job.id} className="rounded-md border border-subtle p-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h3 className="font-medium">Review · {new Date(job.created_at).toLocaleString()}</h3>
              <span className="text-xs rounded bg-layer-2 px-2 py-1 capitalize">{job.status}</span>
            </div>
            {job.error && <p className="text-sm text-red-600 mt-2">{job.error}</p>}
            {job.results.text && (
              <>
                <p className="text-xs mt-3 text-secondary">
                  AI draft · {job.results.model} · Check findings against the evidence
                </p>
                <div className="text-sm mt-2 whitespace-pre-wrap">{job.results.text}</div>
              </>
            )}
            {!!job.evidence.length && (
              <details className="mt-3">
                <summary className="text-sm cursor-pointer font-medium">
                  Captured evidence ({job.evidence.length})
                </summary>
                <ul className="mt-2 space-y-3">
                  {job.evidence.map((source) => (
                    <li key={source.id} className="text-sm rounded bg-layer-2 p-3">
                      <p className="font-medium">{source.title}</p>
                      <p className="text-xs break-all">{source.url}</p>
                      <p className="text-xs mt-1 text-secondary">
                        {source.capture_mode || "Project document"} · {source.captured_at}
                        {source.app_version ? ` · Version ${source.app_version}` : ""}
                      </p>
                      <p className="text-xs break-all text-secondary">Revision: {source.revision}</p>
                      {source.limitations && <p className="text-xs mt-1">{source.limitations}</p>}
                      {source.screenshot && (
                        <img
                          className="mt-2 max-w-full rounded"
                          src={source.screenshot}
                          alt={`Captured app page: ${source.title}`}
                        />
                      )}
                    </li>
                  ))}
                </ul>
              </details>
            )}
          </article>
        ))}
      </div>
    </section>
  );
}
