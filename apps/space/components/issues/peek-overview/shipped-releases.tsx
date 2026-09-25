/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import axios from "axios";
import useSWR from "swr";
import { API_BASE_URL } from "@plane/constants";

type PublishedRelease = { id: string; version: string; name: string };

export function ShippedReleases({ anchor, issueId }: { anchor: string; issueId: string }) {
  const { data } = useSWR(
    `${API_BASE_URL.replace(/\/$/, "")}/api/public/anchor/${anchor}/issues/${issueId}/releases/`,
    async (url: string) => (await axios.get<{ releases: PublishedRelease[] }>(url)).data,
    { shouldRetryOnError: false }
  );
  if (!data?.releases?.length) return null;
  return (
    <section className="rounded-md border border-subtle bg-surface-1 p-3" aria-label="Shipped releases">
      <h5 className="text-13 font-semibold">Shipped in</h5>
      <ul className="mt-1 space-y-1 text-13 text-secondary">
        {data.releases.map((release) => (
          <li key={release.id}>
            {release.version}
            {release.name && release.name !== release.version ? ` — ${release.name}` : ""}
          </li>
        ))}
      </ul>
    </section>
  );
}
