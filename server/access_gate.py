"""Fail-closed Cloudflare Access gate for the MCP origin (Group 3).

The MCP server binds 127.0.0.1:8000 and is fronted by a cloudflared tunnel with
Cloudflare Access in front of the hostname. Access injects a signed JWT in the
`Cf-Access-Jwt-Assertion` header. This module re-verifies that JWT AT THE ORIGIN
(defense in depth: never trust that the edge is the only way in).

Posture is FAIL-CLOSED: when CONSTELLATION_REQUIRE_ACCESS=1 (the daemon default),
every request except an explicit exempt path (/health) must carry a JWT that
verifies against the team's JWKS with the expected audience and issuer. Anything
missing, malformed, mis-audienced, expired, or unverifiable -> 403. If the gate
itself is misconfigured (no team domain / AUD) while required, it also 403s —
a broken gate denies, it never silently opens.

PyJWT + cryptography are already present in the venv (transitive deps); no new
requirements.txt entry is introduced.
"""

import os

from starlette.middleware import Middleware
from starlette.types import Receive, Scope, Send

# Header Cloudflare Access sets on every authenticated request (lower-cased for
# ASGI byte-header comparison).
ACCESS_HEADER = b'cf-access-jwt-assertion'

# Paths reachable without a token (loopback health probe for the daemon/curl).
EXEMPT_PATHS = frozenset({'/health'})


class AccessConfig:
    """Snapshot of the gate's env config, read per-request (cheap, live)."""

    def __init__(self):
        self.require = os.environ.get('CONSTELLATION_REQUIRE_ACCESS', '1') == '1'
        self.team_domain = os.environ.get(
            'CONSTELLATION_ACCESS_TEAM_DOMAIN', '').rstrip('/')
        self.aud = os.environ.get('CONSTELLATION_ACCESS_AUD', '')

    @property
    def certs_url(self) -> str:
        return f'{self.team_domain}/cdn-cgi/access/certs' if self.team_domain else ''

    @property
    def configured(self) -> bool:
        return bool(self.team_domain and self.aud)


# Cache one JWKS client per certs URL (it caches keys internally too).
_jwk_clients = {}


def _signing_key(token: str, certs_url: str):
    import jwt  # PyJWT, present via authlib/fastmcp
    client = _jwk_clients.get(certs_url)
    if client is None:
        client = jwt.PyJWKClient(certs_url)
        _jwk_clients[certs_url] = client
    return client.get_signing_key_from_jwt(token).key


def verify_access_jwt(token, config=None, signing_key_resolver=None) -> bool:
    """Return True iff `token` is a valid Access JWT for this app. Fail-closed:
    any missing input, config gap, or verification error returns False.

    signing_key_resolver(token) -> key lets tests inject a key without network;
    in production the key is fetched from the team JWKS.
    """
    config = config or AccessConfig()
    if not token or not config.configured:
        return False
    try:
        import jwt  # PyJWT
    except Exception:
        return False  # fail closed: cannot verify -> deny
    try:
        if signing_key_resolver is not None:
            key = signing_key_resolver(token)
        else:
            key = _signing_key(token, config.certs_url)
        jwt.decode(
            token,
            key,
            algorithms=['RS256'],
            audience=config.aud,
            issuer=config.team_domain,
        )
        return True
    except Exception:
        return False


def _path(scope: Scope) -> str:
    return scope.get('path', '') or ''


def _bearer_from_headers(scope: Scope):
    for k, v in scope.get('headers', []):
        if k.lower() == ACCESS_HEADER:
            try:
                return v.decode('latin-1')
            except Exception:
                return None
    return None


class AccessGateMiddleware:
    """Pure-ASGI middleware enforcing the fail-closed Access check."""

    def __init__(self, app, config_factory=None, verifier=None):
        self.app = app
        self.config_factory = config_factory or AccessConfig
        self.verifier = verifier or verify_access_jwt

    async def _deny(self, send: Send):
        body = b'{"error":"forbidden","detail":"valid Cloudflare Access JWT required"}'
        await send({
            'type': 'http.response.start',
            'status': 403,
            'headers': [(b'content-type', b'application/json'),
                        (b'content-length', str(len(body)).encode())],
        })
        await send({'type': 'http.response.body', 'body': body})

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope.get('type') != 'http':
            await self.app(scope, receive, send)
            return
        config = self.config_factory()
        if not config.require or _path(scope) in EXEMPT_PATHS:
            await self.app(scope, receive, send)
            return
        token = _bearer_from_headers(scope)
        if not token or not self.verifier(token, config):
            await self._deny(send)
            return
        await self.app(scope, receive, send)


def access_middleware():
    """Middleware list for FastMCP's http_app(middleware=...)."""
    return [Middleware(AccessGateMiddleware)]
