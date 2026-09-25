/*!
 * GSD What's New — embeddable release-notes widget (GSD Plane fork)
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 *
 * Usage:
 *   <script src="/gsd-whats-new.js" data-feed="https://host/api/public/anchor/<anchor>/releases/" defer></script>
 * Optional:
 *   data-app-version="1.2.0"   marks the release matching the running app version
 *
 * Behaviour contract:
 *   - READ-ONLY: performs a single GET (credentials omitted) against data-feed; never posts.
 *   - Does not auto-open; the visitor clicks the launcher.
 *   - All rendering uses textContent (feed strings are never interpreted as HTML).
 *   - UI is isolated from the host page inside a shadow root; host styles cannot leak in.
 */

/* ------------------------------------------------------------------ *
 * Pure helpers (exported as GsdWhatsNew.__internals / module.exports for tests)
 * ------------------------------------------------------------------ */

/**
 * Only absolute http(s) URLs are accepted as feed sources. Anything else
 * (relative paths, protocol-relative hosts, other schemes, URLs embedding
 * credentials) is rejected so the widget can never be pointed at an
 * unintended target by host-page markup.
 */
function gsdIsValidFeedUrl(value) {
  if (typeof value !== "string") return false;
  var trimmed = value.trim();
  if (!trimmed || trimmed.length > 2048) return false;
  try {
    var parsed = new URL(trimmed);
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return false;
    if (!parsed.hostname) return false;
    if (parsed.username || parsed.password) return false;
    return true;
  } catch (err) {
    return false;
  }
}

/**
 * Defensive normalisation of the public feed payload. The feed contract is
 * { project_name, releases: [{id, name, version, notes, published_at,
 * app_version}] } newest-first, published-only — but the widget treats the
 * network as hostile: wrong shapes degrade to empty states instead of
 * throwing, and every rendered field is coerced to a plain string.
 */
function gsdNormalizeReleases(data) {
  var projectName = "";
  var raw = [];
  if (data && typeof data === "object") {
    if (typeof data.project_name === "string") projectName = data.project_name;
    if (Array.isArray(data.releases)) raw = data.releases;
  }
  var releases = [];
  for (var i = 0; i < raw.length; i++) {
    var item = raw[i];
    if (!item || typeof item !== "object") continue;
    var id = typeof item.id === "string" ? item.id : "";
    var name = typeof item.name === "string" ? item.name : "";
    if (!id && !name) continue;
    releases.push({
      id: id,
      name: name,
      version: typeof item.version === "string" ? item.version : "",
      notes: typeof item.notes === "string" ? item.notes : "",
      publishedAt: typeof item.published_at === "string" ? item.published_at : "",
      appVersion: typeof item.app_version === "string" ? item.app_version : "",
    });
  }
  return { projectName: projectName, releases: releases };
}

/** Localised date rendering; returns "" for absent/invalid input. */
function gsdFormatReleaseDate(iso) {
  if (typeof iso !== "string" || !iso) return "";
  var parsed = new Date(iso);
  if (isNaN(parsed.getTime())) return "";
  try {
    return parsed.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
  } catch (err) {
    return parsed.toISOString().slice(0, 10);
  }
}

var gsdInternals = {
  isValidFeedUrl: gsdIsValidFeedUrl,
  normalizeReleases: gsdNormalizeReleases,
  formatReleaseDate: gsdFormatReleaseDate,
};

/* Exposed for host-page debugging and for the behaviour test suite. */
if (typeof globalThis !== "undefined") {
  globalThis.GsdWhatsNew = gsdInternals;
}

/* Node/test consumers get the pure helpers directly. */
if (typeof module !== "undefined" && module.exports) {
  module.exports = gsdInternals;
}

/* ------------------------------------------------------------------ *
 * Widget bootstrap (browser only — skipped under Node/test bundling)
 * ------------------------------------------------------------------ */
if (typeof document === "undefined" || typeof window === "undefined") {
  // Node/test context: pure helpers above are exported; skip browser bootstrap.
} else {
  (function () {
    var script =
      (document.currentScript instanceof HTMLScriptElement && document.currentScript) ||
      document.querySelector("script[data-feed][data-gsd-whats-new]");
    if (!script) return; // not loaded via a script tag; nothing to do

    var feedUrl = script.getAttribute("data-feed") || "";
    var appVersion = script.getAttribute("data-app-version") || "";
    if (!gsdIsValidFeedUrl(feedUrl)) {
      if (window.console && console.warn) {
        console.warn("[gsd-whats-new] disabled: data-feed must be an absolute http(s) URL");
      }
      return;
    }

    var host = document.createElement("div");
    host.setAttribute("data-gsd-whats-new", "");
    var root = host.attachShadow({ mode: "open" });

    var CSS_TEXT =
      ':host{all:initial}' +
      '.launcher{position:fixed;right:20px;bottom:20px;z-index:2147483000;display:inline-flex;align-items:center;gap:8px;' +
      'border:1px solid rgba(0,0,0,.15);border-radius:999px;background:#1f2937;color:#fff;padding:10px 16px;' +
      'font:600 14px/1 system-ui,-apple-system,"Segoe UI",sans-serif;cursor:pointer;box-shadow:0 4px 14px rgba(0,0,0,.25)}' +
      '.launcher:hover{background:#111827}' +
      '.launcher:focus-visible{outline:2px solid #60a5fa;outline-offset:2px}' +
      '.backdrop{position:fixed;inset:0;z-index:2147483001;background:rgba(0,0,0,.45)}' +
      '.dialog{position:fixed;z-index:2147483002;top:50%;left:50%;transform:translate(-50%,-50%);' +
      'width:min(560px,calc(100vw - 32px));max-height:min(640px,calc(100vh - 48px));display:flex;flex-direction:column;' +
      'background:#fff;color:#111827;border-radius:12px;box-shadow:0 12px 40px rgba(0,0,0,.35);' +
      'font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}' +
      '.header{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;padding:16px 20px 8px}' +
      '.title{margin:0;font-size:18px;font-weight:700}' +
      '.project{margin:2px 0 0;color:#6b7280;font-size:12px}' +
      '.close{border:0;background:transparent;color:#6b7280;font-size:20px;line-height:1;cursor:pointer;padding:4px;border-radius:6px}' +
      '.close:hover{background:#f3f4f6;color:#111827}' +
      '.close:focus-visible{outline:2px solid #60a5fa;outline-offset:1px}' +
      '.body{overflow-y:auto;padding:8px 20px 20px}' +
      '.status{padding:32px 8px;text-align:center;color:#6b7280}' +
      '.status[role=alert]{color:#b91c1c}' +
      '.release{border-top:1px solid #e5e7eb;padding:14px 0}' +
      '.release:first-child{border-top:0;padding-top:4px}' +
      '.release-head{display:flex;flex-wrap:wrap;align-items:baseline;gap:8px}' +
      '.release-name{margin:0;font-size:15px;font-weight:650}' +
      '.release-version{color:#374151;background:#f3f4f6;border-radius:6px;padding:1px 7px;font-size:12px;font-weight:600}' +
      '.release-date{color:#6b7280;font-size:12px}' +
      '.badge-current{color:#065f46;background:#d1fae5;border-radius:6px;padding:1px 7px;font-size:11px;font-weight:700;' +
      'text-transform:uppercase;letter-spacing:.03em}' +
      '.notes{margin:8px 0 0;white-space:pre-wrap;overflow-wrap:anywhere;color:#374151}' +
      '@media (max-width:480px){.launcher{right:12px;bottom:12px;padding:9px 13px;font-size:13px}}';

    function el(tag, className, text) {
      var node = document.createElement(tag);
      if (className) node.className = className;
      if (text !== undefined && text !== null) node.textContent = text;
      return node;
    }

    function renderStatus(container, message, isAlert) {
      var node = el("div", "status", message);
      if (isAlert) node.setAttribute("role", "alert");
      container.appendChild(node);
    }

    function renderReleases(container, normalized) {
      if (!normalized.releases.length) {
        renderStatus(container, "No published releases yet.");
        return;
      }
      normalized.releases.forEach(function (release) {
        var card = el("div", "release");
        var head = el("div", "release-head");
        head.appendChild(el("p", "release-name", release.name));
        if (release.version) head.appendChild(el("span", "release-version", release.version));
        if (appVersion && release.appVersion && release.appVersion === appVersion) {
          head.appendChild(el("span", "badge-current", "your version"));
        }
        var date = gsdFormatReleaseDate(release.publishedAt);
        if (date) head.appendChild(el("span", "release-date", date));
        card.appendChild(head);
        if (release.notes) card.appendChild(el("p", "notes", release.notes));
        container.appendChild(card);
      });
    }

    function loadFeed(container) {
      renderStatus(container, "Loading what\u2019s new\u2026");
      fetch(feedUrl, {
        method: "GET",
        credentials: "omit",
        headers: { Accept: "application/json" },
      })
        .then(function (response) {
          if (!response.ok) throw new Error("HTTP " + response.status);
          return response.json();
        })
        .then(function (data) {
          container.textContent = "";
          var normalized = gsdNormalizeReleases(data);
          if (normalized.projectName) {
            container.appendChild(el("p", "project", normalized.projectName));
          }
          renderReleases(container, normalized);
        })
        .catch(function () {
          container.textContent = "";
          renderStatus(container, "Couldn\u2019t load release notes. Try again later.", true);
        });
    }

    var launcher = el("button", "launcher");
    launcher.type = "button";
    launcher.setAttribute("aria-haspopup", "dialog");
    launcher.textContent = "\u2726 What\u2019s new";
    launcher.addEventListener("click", openDialog);
    root.appendChild(launcher);

    var lastFocused = null;

    function closeDialog() {
      var backdrop = root.querySelector(".backdrop");
      if (!backdrop) return;
      if (lastFocused && typeof lastFocused.focus === "function") lastFocused.focus();
      lastFocused = null;
      root.removeChild(backdrop);
    }

    function openDialog() {
      if (root.querySelector(".backdrop")) return;
      lastFocused = document.activeElement;

      var backdrop = el("div", "backdrop");
      var dialog = el("div", "dialog");
      dialog.setAttribute("role", "dialog");
      dialog.setAttribute("aria-modal", "true");
      dialog.setAttribute("aria-label", "What\u2019s new");

      var header = el("div", "header");
      header.appendChild(el("div"));
      var close = el("button", "close");
      close.type = "button";
      close.setAttribute("aria-label", "Close what\u2019s new");
      close.textContent = "\u00d7";
      header.appendChild(close);
      dialog.appendChild(header);

      var body = el("div", "body");
      dialog.appendChild(body);

      backdrop.appendChild(dialog);
      root.appendChild(backdrop);

      close.addEventListener("click", closeDialog);
      backdrop.addEventListener("mousedown", function (event) {
        if (event.target === backdrop) closeDialog();
      });
      dialog.addEventListener("keydown", function (event) {
        if (event.key === "Escape") {
          event.stopPropagation();
          closeDialog();
          return;
        }
        if (event.key === "Tab") {
          var focusables = dialog.querySelectorAll("button, [href]");
          if (!focusables.length) return;
          var first = focusables[0];
          var last = focusables[focusables.length - 1];
          if (event.shiftKey && document.activeElement === first) {
            event.preventDefault();
            last.focus();
          } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault();
            first.focus();
          }
        }
      });

      close.focus();
      loadFeed(body);
    }

    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape") closeDialog();
    });

    var style = document.createElement("style");
    style.textContent = CSS_TEXT;
    root.appendChild(style);
    document.body.appendChild(host);
  })();
}
