import { useParams } from "react-router";
import useSWR from "swr";
type Feed = {
  project_name: string;
  releases: {
    id: string;
    name: string;
    version: string;
    notes: string;
    published_at: string;
    app_version: string | null;
  }[];
};
export default function PublicChangelog() {
  const { anchor = "" } = useParams();
  const { data, error } = useSWR<Feed>(`/api/public/anchor/${encodeURIComponent(anchor)}/releases/`, async (url) => {
    const response = await fetch(url, { credentials: "omit" });
    if (!response.ok) throw new Error("This changelog is unavailable.");
    return response.json();
  });
  return (
    <main className="min-h-screen bg-surface-1 px-5 py-12 text-primary">
      <div className="mx-auto max-w-3xl">
        <p className="text-sm text-secondary">What’s new</p>
        <h1 className="text-30 mt-2 font-semibold">{data?.project_name ?? "Changelog"}</h1>
        {error && (
          <p role="alert" className="mt-8">
            This changelog is unavailable or is no longer public.
          </p>
        )}
        {!data && !error && (
          <p role="status" className="mt-8">
            Loading updates…
          </p>
        )}
        {data?.releases.length === 0 && (
          <p className="mt-8 text-secondary">No releases published yet. Check back for updates.</p>
        )}
        <div className="mt-8 space-y-8">
          {data?.releases.map((release) => (
            <article key={release.id} className="rounded-lg border border-subtle p-6">
              <p className="text-sm text-secondary">
                {release.version} ·{" "}
                <time dateTime={release.published_at}>{new Date(release.published_at).toLocaleDateString()}</time>
              </p>
              <h2 className="mt-2 text-20 font-semibold">{release.name}</h2>
              <div className="text-sm mt-4 leading-7 break-words whitespace-pre-wrap">{release.notes}</div>
            </article>
          ))}
        </div>
      </div>
    </main>
  );
}
