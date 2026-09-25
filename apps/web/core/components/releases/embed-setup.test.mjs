/**
 * Behaviour tests for ReleaseEmbedSetup's pure helpers.
 * Run: esbuild --bundle embed-setup.test.mjs | node --test
 */
import assert from "node:assert/strict";
import test from "node:test";
import { isValidPublicUrl, buildInstallSnippet } from "./embed-urls.ts";

test("isValidPublicUrl accepts absolute http(s) URLs", () => {
  assert.equal(isValidPublicUrl("https://board.example.com/gsd-whats-new.js"), true);
  assert.equal(isValidPublicUrl("http://localhost:8000/api/public/anchor/acme/releases/"), true);
});

test("isValidPublicUrl rejects schemes/paths the widget would refuse", () => {
  assert.equal(isValidPublicUrl("javascript:alert(1)"), false);
  assert.equal(isValidPublicUrl("//cdn.example.com/x.js"), false);
  assert.equal(isValidPublicUrl("/relative/path.js"), false);
  assert.equal(isValidPublicUrl(""), false);
});

test("buildInstallSnippet produces the documented script tag", () => {
  const snippet = buildInstallSnippet(
    "https://board.example.com/api/public/anchor/acme/releases/",
    "https://board.example.com/gsd-whats-new.js"
  );
  assert.equal(
    snippet,
    '<script src="https://board.example.com/gsd-whats-new.js" data-feed="https://board.example.com/api/public/anchor/acme/releases/" defer></script>'
  );
});

test("buildInstallSnippet escapes attribute-breaking quotes", () => {
  const snippet = buildInstallSnippet('https://x.example.com/f"eed', 'https://x.example.com/w"idget.js');
  assert.equal(snippet.includes('data-feed="https://x.example.com/f"eed"'), false);
  assert.equal(snippet.includes("&quot;"), true);
});
