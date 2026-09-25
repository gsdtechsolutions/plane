import { useEffect, useMemo, useState } from "react";
import { useParams, Link } from "react-router";
import useSWR from "swr";
import { EUserPermissions, EUserPermissionsLevel } from "@plane/constants";
import { useUserPermissions } from "@/hooks/store/user";
import { ReleaseService, releaseError } from "@/services/release.service";
import type { ProductRelease, ReleaseInput } from "@/services/release.service";
import { PageReviewPanel } from "@/components/release-intelligence/page-review-panel";
import { ReleaseEmbedSetup } from "@/components/releases/embed-setup";

const service = new ReleaseService();
const blank: ReleaseInput = {
  name: "",
  version: "",
  notes: "",
  app_version: "",
  github_release_id: null,
  pull_request_ids: [],
  issue_ids: [],
};
const inputClass = "w-full rounded-md border border-subtle bg-surface-1 px-3 py-2 text-primary";
const buttonClass = "rounded-md border border-subtle px-3 py-2 text-sm hover:bg-layer-2 disabled:opacity-50";

export default function ReleasesPage() {
  const { workspaceSlug = "", projectId = "" } = useParams();
  const { allowPermissions } = useUserPermissions();
  const canWrite = allowPermissions(
    [EUserPermissions.ADMIN, EUserPermissions.MEMBER],
    EUserPermissionsLevel.PROJECT,
    workspaceSlug,
    projectId
  );
  const canPublish = allowPermissions(
    [EUserPermissions.ADMIN],
    EUserPermissionsLevel.PROJECT,
    workspaceSlug,
    projectId
  );
  const { data, error, mutate } = useSWR(["product-releases", workspaceSlug, projectId], () =>
    service.list(workspaceSlug, projectId)
  );
  const [search, setSearch] = useState("");
  const { data: options } = useSWR(["release-issues", workspaceSlug, projectId, search], () =>
    service.options(workspaceSlug, projectId, search)
  );
  const { data: github, error: githubError } = useSWR(["release-github", workspaceSlug, projectId], () =>
    service.github(workspaceSlug, projectId)
  );
  const [selected, setSelected] = useState<string | null>(null);
  const [form, setForm] = useState<ReleaseInput>(blank);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [failure, setFailure] = useState("");
  const [reviewed, setReviewed] = useState(false);
  const current = data?.releases.find((r) => r.id === selected);
  const published = current?.status === "published";
  const locked = busy || !canWrite || published;
  const items = useMemo(
    () => [...new Map([...(current?.issues ?? []), ...(options?.issues ?? [])].map((i) => [i.id, i])).values()],
    [current?.issues, options?.issues]
  );
  const open = (release: ProductRelease | null) => {
    setSelected(release?.id ?? null);
    setReviewed(false);
    setMessage("");
    setFailure("");
    setForm(
      release
        ? {
            name: release.name,
            version: release.version,
            notes: release.notes,
            app_version: release.app_version,
            github_release_id: release.github_release_id,
            pull_request_ids: release.pull_request_ids,
            issue_ids: release.issues.map((i) => i.id),
          }
        : { ...blank }
    );
  };
  useEffect(() => {
    setSelected(null);
    setForm({ ...blank });
  }, [workspaceSlug, projectId]);
  const update = <K extends keyof ReleaseInput>(key: K, value: ReleaseInput[K]) => {
    setForm((old) => ({ ...old, [key]: value }));
    setReviewed(false);
  };
  const save = async () => {
    setBusy(true);
    setFailure("");
    setMessage("");
    try {
      const result = await service.save(workspaceSlug, projectId, selected, form);
      await mutate();
      open(result);
      setMessage("Draft saved.");
      return result;
    } catch (e) {
      setFailure(releaseError(e));
      return null;
    } finally {
      setBusy(false);
    }
  };
  const action = async (kind: "generate" | "publish" | "unpublish") => {
    setBusy(true);
    setFailure("");
    setMessage("");
    try {
      let id = selected;
      if (kind !== "unpublish") {
        const saved = await service.save(workspaceSlug, projectId, id, form);
        id = saved.id;
        setSelected(id);
      }
      if (!id) return;
      const result = await service.action(workspaceSlug, projectId, id, kind);
      await mutate();
      open(result);
      setMessage(
        kind === "generate"
          ? "AI draft ready. Review its claims before publishing."
          : kind === "publish"
            ? "Published to your changelog."
            : "Returned to draft. It is no longer public."
      );
    } catch (e) {
      setFailure(releaseError(e));
    } finally {
      setBusy(false);
    }
  };
  const origin = typeof window === "undefined" ? "" : window.location.origin;
  const feedUrl = data?.public_anchor ? `${origin}/api/public/anchor/${data.public_anchor}/releases/` : null;
  return (
    <main className="h-full overflow-auto bg-surface-1 p-4 md:p-8">
      <div className="mx-auto max-w-6xl space-y-6">
        <header className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h1 className="text-24 font-semibold">Releases</h1>
            <p className="text-sm mt-1 text-secondary">Turn completed work into a clear story for your customers.</p>
          </div>
          {canWrite && (
            <button className={buttonClass} onClick={() => open(null)} disabled={busy}>
              New release
            </button>
          )}
        </header>
        {error && (
          <p role="alert" className="text-danger-primary">
            {releaseError(error)}
          </p>
        )}
        {!data && !error && <p role="status">Loading releases…</p>}
        <div className="grid gap-6 lg:grid-cols-[260px_minmax(0,1fr)]">
          <nav aria-label="Releases" className="space-y-2">
            {data?.releases.length === 0 && (
              <p className="text-sm rounded-md border border-subtle p-4 text-secondary">
                Your first release starts with a draft. Link work items, write notes, then publish when ready.
              </p>
            )}
            {data?.releases.map((release) => (
              <button
                key={release.id}
                aria-current={selected === release.id ? "page" : undefined}
                onClick={() => open(release)}
                disabled={busy}
                className={`w-full rounded-md border p-3 text-left ${selected === release.id ? "border-accent-strong bg-accent-primary/10" : "border-subtle"}`}
              >
                <span className="block font-medium">{release.name}</span>
                <span className="text-xs mt-1 block text-secondary">
                  {release.version} · {release.status === "published" ? "Published" : "Draft"}
                </span>
              </button>
            ))}
          </nav>
          <section aria-label="Release editor" className="space-y-4 rounded-lg border border-subtle p-4 md:p-6">
            <div className="grid gap-4 sm:grid-cols-2">
              <label className="text-sm space-y-1">
                Release name
                <input
                  className={inputClass}
                  value={form.name}
                  maxLength={200}
                  disabled={locked}
                  onChange={(e) => update("name", e.target.value)}
                />
              </label>
              <label className="text-sm space-y-1">
                Version
                <input
                  className={inputClass}
                  placeholder="v1.0.0"
                  value={form.version}
                  maxLength={100}
                  disabled={locked}
                  onChange={(e) => update("version", e.target.value)}
                />
              </label>
            </div>
            <label className="text-sm block space-y-1">
              App version (optional)
              <input
                className={inputClass}
                value={form.app_version}
                disabled={locked}
                maxLength={100}
                onChange={(e) => update("app_version", e.target.value)}
              />
              <span className="text-xs text-secondary">
                Identify the version these notes describe. This does not verify a deployment.
              </span>
            </label>
            <fieldset disabled={locked} className="space-y-2">
              <legend className="text-sm mb-2 font-medium">Included work items</legend>
              <input
                className={inputClass}
                aria-label="Search work items"
                placeholder="Search work items"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
              <div className="max-h-44 space-y-2 overflow-auto rounded-md border border-subtle p-3">
                {items.map((issue) => (
                  <label key={issue.id} className="text-sm flex items-start gap-2">
                    <input
                      type="checkbox"
                      checked={form.issue_ids.includes(issue.id)}
                      onChange={(e) =>
                        update(
                          "issue_ids",
                          e.target.checked
                            ? [...form.issue_ids, issue.id]
                            : form.issue_ids.filter((id) => id !== issue.id)
                        )
                      }
                    />
                    <span>
                      <span className="text-secondary">{issue.identifier}</span> {issue.name}
                    </span>
                  </label>
                ))}
                {items.length === 0 && <p className="text-sm text-secondary">No matching work items.</p>}
              </div>
            </fieldset>
            <details className="rounded-md border border-subtle p-3">
              <summary className="text-sm cursor-pointer font-medium">GitHub release and pull requests</summary>
              <div className="mt-3 space-y-3">
                {githubError && (
                  <p role="status" className="text-sm text-secondary">
                    GitHub tracking is not available yet. Connect a repository from Development.
                  </p>
                )}
                <label className="text-sm block">
                  GitHub release
                  <select
                    className={inputClass}
                    disabled={locked}
                    value={form.github_release_id ?? ""}
                    onChange={(e) => update("github_release_id", e.target.value || null)}
                  >
                    <option value="">No GitHub release selected</option>
                    {github?.releases?.map((r) => (
                      <option key={r.id} value={r.id}>
                        {r.repository} · {r.tag_name} — {r.name}
                      </option>
                    ))}
                  </select>
                </label>
                <p className="text-xs text-secondary">
                  Select the PRs actually included in this release. Merging a PR does not automatically mean it shipped.
                </p>
                {github?.pull_requests?.map((pr) => (
                  <label key={pr.id} className="text-sm flex gap-2">
                    <input
                      type="checkbox"
                      disabled={locked}
                      checked={form.pull_request_ids.includes(pr.id)}
                      onChange={(e) =>
                        update(
                          "pull_request_ids",
                          e.target.checked
                            ? [...form.pull_request_ids, pr.id]
                            : form.pull_request_ids.filter((id) => id !== pr.id)
                        )
                      }
                    />
                    {pr.repository} #{pr.number} {pr.title} ({pr.state})
                  </label>
                ))}
              </div>
            </details>
            <div className="space-y-2">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <label htmlFor="release-notes" className="text-sm font-medium">
                  Release notes
                </label>
                {canWrite && !published && (
                  <button
                    className={buttonClass}
                    disabled={busy || !form.name || !form.version}
                    onClick={() => void action("generate")}
                  >
                    {busy ? "Working…" : "Draft with AI"}
                  </button>
                )}
              </div>
              <textarea
                id="release-notes"
                className={`${inputClass} min-h-64`}
                value={form.notes}
                maxLength={50000}
                disabled={locked}
                placeholder="What changed, who benefits, and how to use it…"
                onChange={(e) => update("notes", e.target.value)}
              />
              <p className="text-xs text-secondary">
                Only the release name, version, notes, and publication date become public. Linked internal work items
                stay private.
              </p>
            </div>
            {!!current?.sources.length && (
              <details>
                <summary className="text-sm cursor-pointer">Draft evidence ({current.sources.length})</summary>
                <ul className="text-sm mt-2 space-y-1 text-secondary">
                  {current.sources.map((s, index) => (
                    <li key={`${s.id ?? "source"}-${index}`}>{s.title ?? s.type ?? "Source"}</li>
                  ))}
                </ul>
              </details>
            )}
            {failure && (
              <p role="alert" className="text-sm text-danger-primary">
                {failure}
              </p>
            )}
            {message && (
              <p role="status" className="text-sm text-success-primary">
                {message}
              </p>
            )}
            {!published && canPublish && (
              <label className="text-sm flex gap-2">
                <input
                  type="checkbox"
                  checked={reviewed}
                  onChange={(e) => setReviewed(e.target.checked)}
                  disabled={busy}
                />
                I reviewed these notes for public release.
              </label>
            )}
            <div className="flex flex-wrap gap-2">
              {canWrite && !published && (
                <button
                  className={buttonClass}
                  disabled={busy || !form.name.trim() || !form.version.trim()}
                  onClick={() => void save()}
                >
                  Save draft
                </button>
              )}
              {canPublish && !published && (
                <button
                  className={`${buttonClass} bg-accent-primary text-on-color`}
                  disabled={busy || !reviewed || !form.notes.trim() || !form.name.trim() || !form.version.trim()}
                  onClick={() => void action("publish")}
                >
                  Publish release
                </button>
              )}
              {canPublish && published && (
                <button className={buttonClass} disabled={busy} onClick={() => void action("unpublish")}>
                  Unpublish and edit
                </button>
              )}
            </div>
            {published && (
              <p className="text-sm text-secondary">Published notes are locked. Unpublish to prepare changes.</p>
            )}
          </section>
        </div>
        <section className="space-y-3 border-t border-subtle pt-6">
          <h2 className="text-18 font-semibold">Share what shipped</h2>
          {feedUrl ? (
            <>
              <Link
                to={`/changelog/${data?.public_anchor}`}
                target="_blank"
                rel="noopener noreferrer"
                className="text-accent-primary underline"
              >
                Open public changelog
              </Link>
              <ReleaseEmbedSetup feedUrl={feedUrl} scriptUrl={`${origin}/gsd-whats-new.js`} />
            </>
          ) : (
            <p className="text-sm text-secondary">
              Publish this project from its sharing settings to enable the public changelog and app widget. Draft notes
              stay private.
            </p>
          )}
        </section>
        {canWrite && <PageReviewPanel workspaceSlug={workspaceSlug} projectId={projectId} />}
      </div>
    </main>
  );
}
