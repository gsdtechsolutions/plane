"""Bounded public HTTP capture. No cookies, credentials, proxy or browser session."""

import hashlib
import ipaddress
import socket
import time
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit
import urllib3
from django.utils import timezone
from .provider import IntelligenceError

MAX_BYTES = 1024 * 1024


def normalize_origin(value):
    try:
        parsed = urlsplit(value)
        port = parsed.port
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in ("", "/")
        ):
            raise ValueError()
        if port and port != (443 if parsed.scheme == "https" else 80):
            raise ValueError()
        hostname = parsed.hostname.encode("idna").decode("ascii").lower()
        if ":" in hostname:
            hostname = "[" + hostname + "]"
        return urlunsplit((parsed.scheme, hostname, "", "", ""))
    except (TypeError, ValueError, UnicodeError) as exc:
        raise IntelligenceError(
            "Use an HTTP or HTTPS origin without credentials, path, query or a custom port."
        ) from exc


def public_addresses(host, port):
    try:
        addresses = list(dict.fromkeys(item[4][0] for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)))
        if not addresses or any(
            (
                not ipaddress.ip_address(ip).is_global
                or ipaddress.ip_address(ip).is_multicast
                or ipaddress.ip_address(ip).is_reserved
            )
            for ip in addresses
        ):
            raise ValueError()
        return addresses
    except (OSError, ValueError) as exc:
        raise IntelligenceError("The connected origin must resolve exclusively to public IP addresses.") from exc


def selected_url(origin, path):
    if (
        not isinstance(path, str)
        or len(path) > 2048
        or not path.startswith("/")
        or path.startswith("//")
        or "\\" in path
        or "#" in path
    ):
        raise IntelligenceError("Select a path beginning with / on the connected origin.")
    url = urljoin(origin + "/", path)
    if urlsplit(url).netloc != urlsplit(origin).netloc or urlsplit(url).scheme != urlsplit(origin).scheme:
        raise IntelligenceError("Capture paths must remain on the connected origin.")
    return url


class VisibleText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hidden = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "template"):
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "template"):
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, value):
        if not self.hidden and value.strip():
            self.parts.append(value.strip())


def capture_page(origin, path, version=""):
    origin = normalize_origin(origin)
    current = selected_url(origin, path)
    start = time.monotonic()
    for _ in range(4):
        if time.monotonic() - start > 12:
            raise IntelligenceError("Page capture exceeded its time limit.")
        target = urlsplit(current)
        if normalize_origin(urlunsplit((target.scheme, target.netloc, "", "", ""))) != origin:
            raise IntelligenceError("The page redirected outside the configured origin; capture stopped.")
        addresses = public_addresses(target.hostname, target.port or (443 if target.scheme == "https" else 80))
        # Connect to the vetted IP, retaining original TLS SNI and certificate
        # validation. The HTTP client never resolves the hostname a second time.
        options = {"timeout": urllib3.Timeout(connect=3, read=3, total=8), "maxsize": 1}
        if target.scheme == "https":
            pool = urllib3.HTTPSConnectionPool(
                addresses[0],
                port=443,
                server_hostname=target.hostname,
                assert_hostname=target.hostname,
                cert_reqs="CERT_REQUIRED",
                **options,
            )
        else:
            pool = urllib3.HTTPConnectionPool(addresses[0], port=80, **options)
        response = None
        try:
            response = pool.urlopen(
                "GET",
                urlunsplit(("", "", target.path or "/", target.query, "")),
                headers={
                    "Host": target.netloc,
                    "Accept": "text/html,text/plain",
                    "Accept-Encoding": "identity",
                    "User-Agent": "ProjectPageReview/1.0",
                },
                redirect=False,
                retries=False,
                preload_content=False,
            )
            if response.status in (301, 302, 303, 307, 308):
                location = response.headers.get("Location", "")
                redirected = urljoin(current, location)
                parts = urlsplit(redirected)
                if parts.username or parts.password or parts.fragment or not location:
                    raise IntelligenceError("The page returned an unsupported redirect.")
                current = redirected
                continue
            if response.status != 200:
                raise IntelligenceError(
                    f"The page returned HTTP {response.status}; public readable content is required."
                )
            content_type = response.headers.get("Content-Type", "").split(";")[0].lower()
            if content_type not in ("text/html", "text/plain", "application/xhtml+xml"):
                raise IntelligenceError("The page did not return HTML or plain text.")
            data = bytearray()
            while True:
                if time.monotonic() - start > 12:
                    raise IntelligenceError("Page capture exceeded its time limit.")
                chunk = response.read(16384, decode_content=False)
                if not chunk:
                    break
                data.extend(chunk)
                if len(data) > MAX_BYTES:
                    raise IntelligenceError("The page exceeds the 1 MB capture limit.")
            html = bytes(data).decode("utf-8", errors="replace")
            parser = VisibleText()
            parser.feed(html)
            text = " ".join(parser.parts) if content_type != "text/plain" else html
            limited = len(text.strip()) < 100
            return {
                "id": current,
                "title": target.path or "/",
                "url": current,
                "kind": "app_page",
                "capture_mode": "http_only",
                "captured_at": timezone.now().isoformat(),
                "app_version": version,
                "revision": hashlib.sha256(data).hexdigest(),
                "content": text[:10000],
                "evidence_status": "insufficient" if limited else "http_text_only",
                "limitations": "Not browser verified. JavaScript-rendered content, visual appearance, interactions and authenticated pages are not verified.",
            }
        except IntelligenceError:
            raise
        except Exception as exc:
            raise IntelligenceError("The public page could not be captured within the network limits.") from exc
        finally:
            if response is not None:
                response.close()
            pool.close()
    raise IntelligenceError("The page exceeded the redirect limit.")
