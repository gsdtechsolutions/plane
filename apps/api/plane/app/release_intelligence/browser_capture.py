"""Optional isolated public browser capture; never uses a board user's session."""

import base64
import hashlib
import os
import time
from urllib.parse import urlsplit, urlunsplit
import urllib3
from django.utils import timezone
from .capture import MAX_BYTES, normalize_origin, public_addresses, selected_url
from .provider import IntelligenceError


def capture_browser_page(origin, path, version=""):
    if os.environ.get("RELEASE_BROWSER_ENABLED") != "1":
        raise IntelligenceError(
            "Browser review is not configured. Enable the isolated browser capture worker, or choose HTTP evidence."
        )
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise IntelligenceError(
            "Browser capture runtime is missing. Install the optional browser worker requirements and Chromium."
        ) from exc
    origin = normalize_origin(origin)
    url = selected_url(origin, path)
    started = time.monotonic()
    counts = {"requests": 0, "bytes": 0, "blocked": 0}

    def fulfill(route):
        response = None
        pool = None
        try:
            request = route.request
            target = urlsplit(request.url)
            counts["requests"] += 1
            if (
                request.method != "GET"
                or counts["requests"] > 60
                or counts["bytes"] > 8 * MAX_BYTES
                or time.monotonic() - started > 30
            ):
                raise IntelligenceError("Capture budget exceeded.")
            if (
                target.username
                or target.password
                or normalize_origin(urlunsplit((target.scheme, target.netloc, "", "", ""))) != origin
            ):
                raise IntelligenceError("Only connected-origin resources are allowed.")
            ip = public_addresses(target.hostname, target.port or (443 if target.scheme == "https" else 80))[0]
            options = {"timeout": urllib3.Timeout(connect=2, read=2, total=4)}
            if target.scheme == "https":
                pool = urllib3.HTTPSConnectionPool(
                    ip,
                    port=443,
                    server_hostname=target.hostname,
                    assert_hostname=target.hostname,
                    cert_reqs="CERT_REQUIRED",
                    **options,
                )
            else:
                pool = urllib3.HTTPConnectionPool(ip, port=80, **options)
            response = pool.urlopen(
                "GET",
                urlunsplit(("", "", target.path or "/", target.query, "")),
                headers={"Host": target.netloc, "Accept-Encoding": "identity"},
                redirect=False,
                retries=False,
                preload_content=False,
            )
            data = bytearray()
            while True:
                if time.monotonic() - started > 30:
                    raise IntelligenceError("Capture timed out.")
                chunk = response.read(16384, decode_content=False)
                if not chunk:
                    break
                data.extend(chunk)
                counts["bytes"] += len(chunk)
                if len(data) > MAX_BYTES or counts["bytes"] > 8 * MAX_BYTES:
                    raise IntelligenceError("Capture size exceeded.")
            # Never forward cookies, auth challenges or external redirect access.
            headers = {
                k: v
                for k, v in response.headers.items()
                if k.lower() in ("content-type", "location", "content-security-policy")
            }
            route.fulfill(status=response.status, headers=headers, body=bytes(data))
        except Exception:
            counts["blocked"] += 1
            route.abort("blockedbyclient")
        finally:
            if response is not None:
                response.close()
            if pool is not None:
                pool.close()

    try:
        with sync_playwright() as runtime:
            browser = runtime.chromium.launch(
                headless=True,
                chromium_sandbox=True,
                args=[
                    "--disable-background-networking",
                    "--disable-quic",
                    "--disable-dns-prefetch",
                    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
                    "--proxy-server=http://127.0.0.1:9",
                    "--proxy-bypass-list=<-loopback>",
                ],
            )
            try:
                context = browser.new_context(
                    service_workers="block", accept_downloads=False, viewport={"width": 1280, "height": 900}
                )
                context.route("**/*", fulfill)
                context.route_web_socket("**/*", lambda ws: ws.close())
                page = context.new_page()
                context.on("page", lambda popup: popup.close() if popup != page else None)
                result = page.goto(url, wait_until="domcontentloaded", timeout=35000)
                if result is None or result.status >= 400:
                    raise IntelligenceError("The connected page did not load successfully in the browser.")
                page.wait_for_timeout(1500)
                if (
                    normalize_origin(urlunsplit((urlsplit(page.url).scheme, urlsplit(page.url).netloc, "", "", "")))
                    != origin
                ):
                    raise IntelligenceError("Browser navigation left the configured origin.")
                text = page.locator("body").inner_text(timeout=3000)[:10000]
                screenshot = page.screenshot(type="jpeg", quality=55, full_page=False, timeout=3000)
                if len(screenshot) > MAX_BYTES:
                    raise IntelligenceError("The screenshot exceeds the capture limit.")
                return {
                    "id": page.url,
                    "title": page.title()[:255],
                    "url": page.url,
                    "kind": "app_page",
                    "capture_mode": "browser_rendered",
                    "captured_at": timezone.now().isoformat(),
                    "app_version": version,
                    "revision": hashlib.sha256(text.encode() + screenshot).hexdigest(),
                    "content": text,
                    "screenshot": "data:image/jpeg;base64," + base64.b64encode(screenshot).decode(),
                    "evidence_status": "partial" if counts["blocked"] or len(text.strip()) < 100 else "rendered",
                    "limitations": f"Public rendered-text review. {counts['blocked']} resources blocked or unavailable. No authenticated access or interaction flows tested. Screenshot retained for staff; AI analysis uses text only.",
                }
            finally:
                browser.close()
    except IntelligenceError:
        raise
    except Exception as exc:
        raise IntelligenceError(
            "Browser capture failed. Check the isolated worker runtime or use HTTP evidence."
        ) from exc
