/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// Regression tests for the /m/auth bridge's open-redirect guard. Run standalone
// with node's test runner (no extra deps): `node --test app/m/auth` after
// transpiling, e.g. `esbuild --bundle --platform=node safe-redirect-path.test.ts`.

import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { isSafeRedirectPath } from "./safe-redirect-path";

describe("isSafeRedirectPath", () => {
  it("accepts single-root-relative paths", () => {
    assert.equal(isSafeRedirectPath("/"), true);
    assert.equal(isSafeRedirectPath("/workspace/acme"), true);
    assert.equal(isSafeRedirectPath("/settings/profile/general"), true);
    assert.equal(isSafeRedirectPath("/sign-in?foo=bar"), true);
  });

  it("rejects empty and non-root-relative values", () => {
    assert.equal(isSafeRedirectPath(""), false);
    assert.equal(isSafeRedirectPath(null), false);
    assert.equal(isSafeRedirectPath(undefined), false);
    assert.equal(isSafeRedirectPath("workspace/acme"), false);
    assert.equal(isSafeRedirectPath("workspace"), false);
  });

  it("rejects protocol-relative hosts (incl. backslash variants)", () => {
    assert.equal(isSafeRedirectPath("//host"), false);
    assert.equal(isSafeRedirectPath("/\\host"), false);
    assert.equal(isSafeRedirectPath("\\/host"), false);
    assert.equal(isSafeRedirectPath("\\\\host"), false);
  });

  it("rejects URL-encoded variants once decoded (as URLSearchParams hands them over)", () => {
    assert.equal(isSafeRedirectPath(decodeURIComponent("%2F%2Fhost")), false); // //host
    assert.equal(isSafeRedirectPath(decodeURIComponent("%5C")), false); // backslash
    assert.equal(isSafeRedirectPath(decodeURIComponent("%2F%5Chost")), false); // /\host
    assert.equal(isSafeRedirectPath(decodeURIComponent("%2F%2Fhost%2Fx")), false); // //host/x
    assert.equal(isSafeRedirectPath(decodeURIComponent("https%3A%2F%2Fevil")), false); // https://evil
  });

  it("rejects absolute URLs and schemes", () => {
    assert.equal(isSafeRedirectPath("https://evil"), false);
    assert.equal(isSafeRedirectPath("http://evil/x"), false);
    assert.equal(isSafeRedirectPath("ftp://host/file"), false);
    assert.equal(isSafeRedirectPath("javascript:alert(1)"), false);
    assert.equal(isSafeRedirectPath("data:text/html,hi"), false);
    assert.equal(isSafeRedirectPath("/https://evil"), false);
    assert.equal(isSafeRedirectPath("/x?next=https://evil"), false);
  });

  it("rejects backslashes and control characters", () => {
    assert.equal(isSafeRedirectPath("/a\\b"), false);
    assert.equal(isSafeRedirectPath("/a\nb"), false);
    assert.equal(isSafeRedirectPath("/a\rb"), false);
    assert.equal(isSafeRedirectPath("/a\tb"), false);
    assert.equal(isSafeRedirectPath("/a\u0000b"), false);
    assert.equal(isSafeRedirectPath("/a\u001bb"), false);
    assert.equal(isSafeRedirectPath("/a\u007fb"), false);
  });
});
