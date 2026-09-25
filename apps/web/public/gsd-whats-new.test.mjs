/**
 * Behaviour tests for the embeddable What's New widget's pure logic.
 * The widget is a classic browser script, so the suite evaluates its source
 * and reads the helpers off globalThis.GsdWhatsNew.
 * Run: esbuild --bundle gsd-whats-new.test.mjs | node --test
 */
import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

// resolved relative to apps/web/public (the documented cwd for this suite)
const widgetSource = readFileSync("gsd-whats-new.js", "utf8");
(0, eval)(widgetSource);
const { isValidFeedUrl, normalizeReleases, formatReleaseDate } = globalThis.GsdWhatsNew;

test("isValidFeedUrl accepts absolute http(s) URLs", () => {
  assert.equal(isValidFeedUrl("https://plane.example.com/api/public/anchor/acme/releases/"), true);
  assert.equal(isValidFeedUrl("http://localhost:8000/api/public/anchor/acme/releases/"), true);
});

test("isValidFeedUrl rejects non-http schemes, relative paths, and embedded credentials", () => {
  assert.equal(isValidFeedUrl("javascript:alert(1)"), false);
  assert.equal(isValidFeedUrl("file:///etc/passwd"), false);
  assert.equal(isValidFeedUrl("//evil.example.com/feed"), false);
  assert.equal(isValidFeedUrl("/api/public/anchor/acme/releases/"), false);
  assert.equal(isValidFeedUrl("https://user:pass@evil.example.com/feed"), false);
  assert.equal(isValidFeedUrl(""), false);
  assert.equal(isValidFeedUrl(undefined), false);
  assert.equal(isValidFeedUrl("https://ok.example.com/" + "x".repeat(3000)), false);
});

test("normalizeReleases maps the documented feed contract to render strings", () => {
  const { projectName, releases } = normalizeReleases({
    project_name: "Aether Park",
    releases: [
      {
        id: "r1",
        name: "Seat selection",
        version: "1.2.0",
        notes: "Interactive seat map.",
        published_at: "2026-09-24T12:00:00Z",
        app_version: "1.2.0",
      },
    ],
  });
  assert.equal(projectName, "Aether Park");
  assert.equal(releases.length, 1);
  assert.deepEqual(releases[0], {
    id: "r1",
    name: "Seat selection",
    version: "1.2.0",
    notes: "Interactive seat map.",
    publishedAt: "2026-09-24T12:00:00Z",
    appVersion: "1.2.0",
  });
});

test("normalizeReleases degrades hostile payloads to empty states instead of throwing", () => {
  for (const hostile of [null, undefined, 42, "nope", {}, { releases: "nope" }, { releases: [null, 7, {}] }]) {
    const out = normalizeReleases(hostile);
    assert.equal(typeof out.projectName, "string");
    assert.equal(Array.isArray(out.releases), true);
  }
  const skipped = normalizeReleases({ releases: [null, 7, {}, { id: "ok", name: "Fine" }] });
  assert.equal(skipped.releases.length, 1);
  assert.equal(skipped.releases[0].id, "ok");
});

test("normalizeReleases coerces non-string fields to empty strings (never objects into render)", () => {
  const out = normalizeReleases({
    releases: [{ id: { evil: 1 }, name: "x", notes: { html: "<script>" }, published_at: 5 }],
  });
  assert.equal(out.releases[0].id, "");
  assert.equal(out.releases[0].name, "x");
  assert.equal(out.releases[0].notes, "");
  assert.equal(out.releases[0].publishedAt, "");
});

test("formatReleaseDate renders valid ISO dates and blanks invalid input", () => {
  const out = formatReleaseDate("2026-09-24T12:00:00Z");
  assert.match(out, /2026/);
  assert.equal(formatReleaseDate(""), "");
  assert.equal(formatReleaseDate("not-a-date"), "");
  assert.equal(formatReleaseDate(undefined), "");
});
