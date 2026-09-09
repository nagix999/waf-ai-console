"""Exact-origin protection for cookie-authenticated browser writes.

No request body, credentials, or untrusted header value is logged or returned.
Machine clients without an administrator session retain API-key authentication.
"""
import ipaddress
import re
from urllib.parse import urlsplit

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
AUTH_PATHS = frozenset({"/api/v1/auth/login", "/api/v1/auth/logout"})
HEALTH_PATHS = frozenset({"/health/live", "/health/ready"})
DNS_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")


def normalize_origin(value: str, *, referer: bool = False) -> str:
    """Accept one HTTP(S) origin, never a list, wildcard, or credentialed URL."""
    if (
        not value
        or len(value) > (8192 if referer else 2048)
        or not value.isascii()
        or any(ord(char) <= 32 or ord(char) == 127 for char in value)
        or "\\" in value
    ):
        raise ValueError("invalid origin")
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("invalid origin")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("invalid origin")
        authority_pattern = r"\[[0-9a-fA-F:.]+\](?::[0-9]{1,5})?" if parsed.netloc.startswith("[") else r"[a-zA-Z0-9.-]+(?::[0-9]{1,5})?"
        if not re.fullmatch(authority_pattern, parsed.netloc):
            raise ValueError("invalid origin")
        if not referer and (parsed.path or "?" in value or "#" in value):
            raise ValueError("invalid origin")
        if referer and "#" in value:
            raise ValueError("invalid origin")
        host, port = parsed.hostname, parsed.port
        if not host or "%" in host or parsed.netloc.endswith(":"):
            raise ValueError("invalid origin")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            if len(host) > 253 or not all(DNS_LABEL.fullmatch(label) for label in host.split(".")):
                raise ValueError("invalid origin") from None
        else:
            host = f"[{address.compressed}]" if address.version == 6 else address.compressed
        if port is not None and not 1 <= port <= 65535:
            raise ValueError("invalid origin")
        suffix = "" if port in {None, 443 if parsed.scheme == "https" else 80} else f":{port}"
        return f"{parsed.scheme}://{host}{suffix}"
    except (ValueError, UnicodeError):
        raise ValueError("invalid origin") from None


def _values(scope: Scope, name: bytes) -> list[str]:
    return [value.decode("latin-1") for key, value in scope.get("headers", []) if key.lower() == name]


class BrowserSecurityMiddleware:
    """Runs inside SessionMiddleware and before route/body/auth-key processing."""

    def __init__(self, app: ASGIApp, public_origin: str = "") -> None:
        self.app = app
        self.public_origin = public_origin

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        method = scope["method"].upper()
        path = scope["path"].rstrip("/")

        # Health probes intentionally use the private listener. No other route
        # may accept an alternate authority when a public origin is configured.
        if self.public_origin and not (method in {"GET", "HEAD"} and path in HEALTH_PATHS):
            hosts = _values(scope, b"host")
            try:
                target = normalize_origin(f"{urlsplit(self.public_origin).scheme}://{hosts[0]}") if len(hosts) == 1 else ""
            except ValueError:
                target = ""
            if target != self.public_origin:
                await self._reject(scope, receive, send, "untrusted_host", 421)
                return

        # A supplied API key never bypasses protection for an authenticated
        # administrator: get_principal also gives the session precedence.
        browser_write = method not in SAFE_METHODS and (
            path in AUTH_PATHS or request.session.get("admin_authenticated") is True
        )
        if browser_write:
            origins = _values(scope, b"origin")
            referers = _values(scope, b"referer")
            fetch_sites = _values(scope, b"sec-fetch-site")
            if not origins and not referers:
                await self._reject(scope, receive, send, "csrf_origin_required")
                return
            try:
                if len(origins) > 1 or len(referers) > 1 or len(fetch_sites) > 1:
                    raise ValueError("invalid origin")
                if fetch_sites and fetch_sites[0] not in {"same-origin", "none"}:
                    raise ValueError("invalid origin")
                supplied = normalize_origin(origins[0]) if origins else normalize_origin(referers[0], referer=True)
                # In production this is fixed configuration, not Host/XFH/XFP.
                # Local development accepts only the actual request authority.
                if self.public_origin:
                    expected = self.public_origin
                else:
                    hosts = _values(scope, b"host")
                    if len(hosts) != 1:
                        raise ValueError("invalid origin")
                    expected = normalize_origin(f"{scope['scheme']}://{hosts[0]}")
                if supplied != expected:
                    raise ValueError("invalid origin")
            except ValueError:
                await self._reject(scope, receive, send, "csrf_origin_invalid")
                return

        await self.app(scope, receive, send)

    @staticmethod
    async def _reject(scope: Scope, receive: Receive, send: Send, detail: str, status: int = 403) -> None:
        response = JSONResponse({"detail": detail}, status_code=status, headers={"Cache-Control": "no-store"})
        await response(scope, receive, send)
