"""Tests for the fail-closed Cloudflare Access gate (Group 3).

verify_access_jwt is exercised with a locally-generated RSA key (JWKS injected
via signing_key_resolver, so no network). The middleware is driven directly over
ASGI (no httpx) with a stub verifier to isolate its allow/deny logic.
"""

import asyncio
import datetime
import unittest
from types import SimpleNamespace

from cryptography.hazmat.primitives.asymmetric import rsa
import jwt

from server.access_gate import AccessGateMiddleware, verify_access_jwt

TEAM = 'https://testteam.cloudflareaccess.com'
AUD = 'test-access-app-aud-tag'

_PRIV = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_PUB = _PRIV.public_key()


def _cfg(require=True, team=TEAM, aud=AUD):
    return SimpleNamespace(
        require=require, team_domain=team, aud=aud,
        configured=bool(team and aud),
        certs_url=f'{team}/cdn-cgi/access/certs' if team else '')


def _token(aud=AUD, iss=TEAM, exp_delta=3600, key=_PRIV):
    now = datetime.datetime.now(datetime.timezone.utc)
    return jwt.encode(
        {'aud': aud, 'iss': iss, 'sub': 'user@example.com',
         'iat': now, 'exp': now + datetime.timedelta(seconds=exp_delta)},
        key, algorithm='RS256')


def _resolver(_token):
    return _PUB


class TestVerifyAccessJwt(unittest.TestCase):
    def test_valid_token_present(self):
        self.assertTrue(verify_access_jwt(_token(), _cfg(), _resolver))

    def test_absent_token(self):
        self.assertFalse(verify_access_jwt(None, _cfg(), _resolver))
        self.assertFalse(verify_access_jwt('', _cfg(), _resolver))

    def test_garbage_token(self):
        self.assertFalse(verify_access_jwt('not.a.jwt', _cfg(), _resolver))
        self.assertFalse(verify_access_jwt('garbage', _cfg(), _resolver))

    def test_wrong_audience_rejected(self):
        self.assertFalse(
            verify_access_jwt(_token(aud='some-other-app'), _cfg(), _resolver))

    def test_wrong_issuer_rejected(self):
        self.assertFalse(
            verify_access_jwt(_token(iss='https://evil.cloudflareaccess.com'),
                              _cfg(), _resolver))

    def test_expired_rejected(self):
        self.assertFalse(
            verify_access_jwt(_token(exp_delta=-10), _cfg(), _resolver))

    def test_wrong_signing_key_rejected(self):
        other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.assertFalse(
            verify_access_jwt(_token(key=other), _cfg(), _resolver))

    def test_unconfigured_fails_closed(self):
        # required but no team domain / AUD -> deny even with a "valid" token
        self.assertFalse(
            verify_access_jwt(_token(), _cfg(team='', aud=''), _resolver))


# --- ASGI driver helpers ---
class _DummyApp:
    def __init__(self):
        self.called = False

    async def __call__(self, scope, receive, send):
        self.called = True
        await send({'type': 'http.response.start', 'status': 200, 'headers': []})
        await send({'type': 'http.response.body', 'body': b'ok'})


def _http_scope(path='/mcp', token=None):
    headers = []
    if token is not None:
        headers.append((b'cf-access-jwt-assertion', token.encode()))
    return {'type': 'http', 'path': path, 'headers': headers}


def _drive(mw, scope):
    sent = []

    async def receive():
        return {'type': 'http.request', 'body': b'', 'more_body': False}

    async def send(msg):
        sent.append(msg)

    asyncio.run(mw(scope, receive, send))
    for m in sent:
        if m['type'] == 'http.response.start':
            return m['status']
    return None


class TestAccessGateMiddleware(unittest.TestCase):
    def _mw(self, downstream, require=True, verifier=None):
        return AccessGateMiddleware(
            downstream,
            config_factory=lambda: _cfg(require=require),
            verifier=verifier or (lambda t, c: t == 'good'))

    def test_denies_when_token_absent(self):
        app = _DummyApp()
        status = _drive(self._mw(app), _http_scope(token=None))
        self.assertEqual(status, 403)
        self.assertFalse(app.called)

    def test_denies_when_token_garbage(self):
        app = _DummyApp()
        status = _drive(self._mw(app), _http_scope(token='garbage'))
        self.assertEqual(status, 403)
        self.assertFalse(app.called)

    def test_allows_when_token_valid(self):
        app = _DummyApp()
        status = _drive(self._mw(app), _http_scope(token='good'))
        self.assertEqual(status, 200)
        self.assertTrue(app.called)

    def test_bypass_when_not_required(self):
        app = _DummyApp()
        status = _drive(self._mw(app, require=False), _http_scope(token=None))
        self.assertEqual(status, 200)
        self.assertTrue(app.called)

    def test_health_path_exempt(self):
        app = _DummyApp()
        status = _drive(self._mw(app), _http_scope(path='/health', token=None))
        self.assertEqual(status, 200)
        self.assertTrue(app.called)


if __name__ == '__main__':
    unittest.main()
