#!/usr/bin/env node
// Browser auth-session journey with Playwright: sign in through the UI, reload two
// workspace pages, and read the session cookie the browser holds.
//   node browser-session.mjs <evidence-dir>
// Env: BASE (http://localhost:$PLANE_VERIFY_PORT, default 38080), EMAIL, PASSWORD, WORKSPACE (verify), MIN_AGE (2592000),
//      PLAYWRIGHT_CORE (path to playwright-core), CHROME (executable), CHROME_LIBS (LD_LIBRARY_PATH).
import { mkdirSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";

const env = process.env;
const out = process.argv[2] || `${env.HOME}/.local/state/plane-verify/evidence/${new Date().toISOString().replace(/[-:.]/g, "")}-browser-session`;
const BASE = env.BASE || `http://localhost:${env.PLANE_VERIFY_PORT || 38080}`;
const WS = env.WORKSPACE || "verify";
const MIN_AGE = Number(env.MIN_AGE || 2592000);
const require = createRequire(import.meta.url);
const { chromium } = require(env.PLAYWRIGHT_CORE || "/workspace/gsd-plane/e2e/node_modules/playwright-core");
mkdirSync(out, { recursive: true });

const log = [];
const say = (s) => { console.log(s); log.push(s); };
let fail = false;
const check = (ok, msg) => { say(`${ok ? "ok" : "FAIL"}: ${msg}`); if (!ok) fail = true; };

const browser = await chromium.launch({
  executablePath: env.CHROME || `${env.HOME}/.cache/ms-playwright/chromium-1243/chrome-linux64/chrome`,
  headless: true,
  env: { ...env, LD_LIBRARY_PATH: env.CHROME_LIBS || "/workspace/gsd-plane/chromelibs/usr/lib/x86_64-linux-gnu" },
});
try {
  const page = await (await browser.newContext({ viewport: { width: 1440, height: 900 } })).newPage();
  let throttled = 0;
  let step = "sign-in page"; const perStep = {};
  page.on("request", () => { perStep[step] = (perStep[step] || 0) + 1; });
  page.on("response", (r) => { if (r.status() === 429) { throttled++; say(`429 during ${step}: ${new URL(r.url()).pathname}`); } });
  // Signed in means the workspace document loaded with 200 and the sidebar link to this
  // workspace's projects rendered; a URL alone also matches an error page.
  let docStatus = 0;
  page.on("response", (r) => { if (r.request().isNavigationRequest() && r.frame() === page.mainFrame()) docStatus = r.status(); });
  const signedIn = async () => {
    await page.waitForURL((u) => u.pathname.startsWith(`/${WS}/`), { timeout: 30000 }).catch(() => {});
    const sidebar = await page.locator(`a[href^="/${WS}/projects"]`).first().waitFor({ timeout: 30000 }).then(() => true, () => false);
    say(`  document ${docStatus}, workspace sidebar ${sidebar ? "rendered" : "missing"}`);
    return docStatus === 200 && sidebar;
  };

  await page.goto(`${BASE}/`, { waitUntil: "domcontentloaded" });
  await page.locator("#email").fill(env.EMAIL || "verify-user@example.com");
  await page.locator('button[type="submit"]').first().click();
  await page.locator("#password").fill(env.PASSWORD || "Verify-Plane-2026!");
  step = "workspace after sign-in";
  await page.locator('form:has(#password) button[type="submit"]').first().click();
  check(await signedIn(), `sign-in lands on /${WS}/ (${page.url()})`);
  await page.screenshot({ path: `${out}/1-after-signin.png` });

  const cookie = (await page.context().cookies()).find((c) => c.name === "session-id");
  const lifetime = cookie ? Math.round(cookie.expires - Date.now() / 1000) : 0;
  check(lifetime >= MIN_AGE - 120, `browser session-id expires in ${lifetime}s (want >= ${MIN_AGE})`);

  step = "reload home";
  await page.reload({ waitUntil: "domcontentloaded" });
  check(await signedIn(), `reload of ${new URL(page.url()).pathname} stays signed in`);
  await page.screenshot({ path: `${out}/2-after-reload-home.png` });

  step = "projects";
  await page.goto(`${BASE}/${WS}/projects/`, { waitUntil: "domcontentloaded" });
  await page.waitForLoadState("networkidle").catch(() => {});
  step = "reload projects";
  await page.reload({ waitUntil: "domcontentloaded" });
  check(await signedIn(), `reload of /${WS}/projects/ stays signed in (${page.url()})`);
  await page.screenshot({ path: `${out}/3-after-reload-projects.png` });
  say(`requests per page load: ${JSON.stringify(perStep)}`);
  check(throttled === 0, `no 429 responses from the web rate limit (${throttled})`);
} catch (e) {
  check(false, `journey threw: ${e.message}`);
} finally {
  await browser.close();
}
say(fail ? "RESULT: FAIL" : "PASS");
say(`evidence: ${out}`);
writeFileSync(`${out}/transcript.txt`, log.join("\n") + "\n");
process.exit(fail ? 1 : 0);
