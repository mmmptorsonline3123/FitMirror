"""SSRF-safe, robots.txt-respecting HTTP fetcher for product pages.

The public-IP check runs inside the connection layer (httpcore network backend), i.e. on the address actually being
connected to after DNS resolution, so DNS-rebinding and redirects to internal hosts are blocked too. TLS still verifies
the original hostname (httpcore passes it as SNI/server_hostname).
"""
import ipaddress
import socket
import time
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser
import httpcore
import httpx

USER_AGENT = "FitMirrorBot/1.0"
ROBOTS_AGENT = "FitMirrorBot"
ALLOWED_PORTS = {None, 80, 443}
MAX_REDIRECTS = 5


class FetchError(Exception):
    def __init__(self, code: str, reason: str):
        super().__init__(reason)
        self.code, self.reason = code, reason


class BlockedAddress(httpcore.ConnectError):
    pass


def is_public(ip: str) -> bool:
    addr = ipaddress.ip_address(ip.split("%")[0])
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return addr.is_global and not addr.is_multicast


class GuardedBackend(httpcore.SyncBackend):
    def __init__(self, allow: set[str], resolve=socket.getaddrinfo):
        self.allow, self.resolve = allow, resolve  # allow: "host:port" exceptions, test/E2E only

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        if f"{host}:{port}" in self.allow:
            return super().connect_tcp(host, port, timeout, local_address, socket_options)
        try:
            ips = [ai[4][0] for ai in self.resolve(host, port, type=socket.SOCK_STREAM)]
        except OSError as e:
            raise httpcore.ConnectError(f"DNS failed: {e}") from e
        if not ips or not all(is_public(ip) for ip in ips):
            raise BlockedAddress("refusing to connect to a non-public address")
        return super().connect_tcp(ips[0], port, timeout, local_address, socket_options)  # connect to the IP we checked


def make_client(allow: set[str] = frozenset(), timeout: float = 10.0, transport: httpx.BaseTransport | None = None) -> httpx.Client:
    if transport is None:
        transport = httpx.HTTPTransport()
        # Swap in a pool with the guarded backend (httpx 0.28 has no public hook for this; pinned in requirements, covered by tests).
        transport._pool = httpcore.ConnectionPool(ssl_context=httpx.create_ssl_context(), network_backend=GuardedBackend(set(allow)))
    return httpx.Client(transport=transport, timeout=timeout, follow_redirects=False, trust_env=False,
                        headers={"User-Agent": USER_AGENT, "Accept-Language": "en"})


def check_url(url: str, allow: set[str] = frozenset()) -> str:
    try:
        u = urlsplit(url.strip())
        port = u.port
    except ValueError:
        raise FetchError("bad_url", "That doesn't look like a valid web address.")
    if u.scheme not in ("http", "https") or not u.hostname:
        raise FetchError("bad_url", "Only http(s) product links are supported.")
    if u.username or u.password:
        raise FetchError("bad_url", "Links with embedded usernames or passwords aren't supported.")
    if port not in ALLOWED_PORTS and f"{u.hostname}:{port}" not in allow:
        raise FetchError("bad_url", "Only standard web ports (80/443) are supported.")
    return url.strip()


class Fetcher:
    def __init__(self, client: httpx.Client, allow: set[str] = frozenset(), deadline_s: float = 25.0):
        self.client, self.allow = client, allow
        self.deadline = time.monotonic() + deadline_s
        self._robots: dict[str, RobotFileParser | None] = {}

    def _get(self, url: str, max_bytes: int, accept: tuple[str, ...]) -> tuple[str, bytes, str]:
        """GET with manual redirects (each hop re-checked). Returns (final_url, body, content_type)."""
        for _ in range(MAX_REDIRECTS + 1):
            if time.monotonic() > self.deadline:
                raise FetchError("timeout", "The page took too long to load.")
            url = check_url(url, self.allow)
            try:
                with self.client.stream("GET", url) as r:
                    if r.is_redirect and "location" in r.headers:
                        url = urljoin(url, r.headers["location"])
                        continue
                    if r.status_code != 200:
                        raise FetchError("http_error", f"The site answered with an error ({r.status_code}).")
                    ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
                    if accept and not ctype.startswith(accept):
                        raise FetchError("wrong_type", f"Expected {' or '.join(accept)}, got {ctype or 'unknown'}.")
                    body = b""
                    for chunk in r.iter_bytes():
                        body += chunk
                        if len(body) > max_bytes:
                            raise FetchError("too_large", "The page or image is too large.")
                    return url, body, ctype
            except httpx.HTTPError as e:
                if isinstance(e.__cause__, BlockedAddress):  # httpx re-wraps httpcore errors; ours rides along as __cause__
                    raise FetchError("unsafe_url", "That link points to a private or local network address.")
                raise FetchError("fetch_failed", f"Couldn't reach the site ({type(e).__name__}).")
        raise FetchError("fetch_failed", "Too many redirects.")

    def allowed(self, url: str) -> bool:
        """robots.txt per RFC 9309: 4xx -> allow all; 5xx / unreachable -> disallow all."""
        u = urlsplit(url)
        origin = f"{u.scheme}://{u.netloc}"
        if origin not in self._robots:
            rp = RobotFileParser()
            try:
                _, body, _ = self._get(origin + "/robots.txt", 512_000, ())
                rp.parse(body.decode("utf-8", "replace").splitlines())
            except FetchError as e:
                if e.code == "http_error" and "(4" in e.reason:
                    rp = None  # no robots.txt -> everything allowed
                elif e.code == "unsafe_url":
                    raise
                else:
                    rp = RobotFileParser()
                    rp.disallow_all = True
            self._robots[origin] = rp
        rp = self._robots[origin]
        return rp is None or rp.can_fetch(ROBOTS_AGENT, url)

    def get(self, url: str, max_bytes: int, accept: tuple[str, ...]) -> tuple[str, bytes, str]:
        url = check_url(url, self.allow)
        if not self.allowed(url):
            raise FetchError("blocked_by_robots", "This site doesn't allow automated access to that page.")
        return self._get(url, max_bytes, accept)
