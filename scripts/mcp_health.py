"""MCP server health checks (Phase 0, Group 2).

Four checks, callable individually or via main():

  1. stdio boot     — spawn server/mcp_server.py over stdio, complete the MCP
                      handshake, assert tools/list returns EXACTLY the 7
                      canonical tools (missing or extra tools both fail).
  2. http binding   — spawn the server with --transport http on a scratch
                      port, assert `lsof` shows it listening on 127.0.0.1
                      only. `*:port` or `0.0.0.0:port` is a hard failure.
                      If something is already serving the production port
                      (8000), its binding is asserted too.
  3. round trips    — get_stats returns sane values; a [PHASE0-HEALTHCHECK]
                      note is added to a designated test conversation, read
                      back, deleted, and the final note count must equal the
                      starting count.
  4. divergence     — if the public tunnel answers, compare its get_stats
                      fingerprint (total messages + max date) against the
                      local instance. Mismatch = WARN loudly with both
                      fingerprints (tunnel served by a different dataset).

Used by scripts/mcp_health.sh and tests/test_mcp_health.py.
Run directly:  .venv/bin/python scripts/mcp_health.py [--skip-network]
"""

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

VPY = str(REPO / '.venv' / 'bin' / 'python')
SERVER = str(REPO / 'server' / 'mcp_server.py')

CANONICAL_TOOLS = {
    'search_conversations', 'get_conversation', 'list_conversations',
    'list_recent_conversations', 'add_conversation_note',
    'delete_conversation_note', 'get_stats',
}
# Designated test conversation (verified present in the local index; already
# carries permanent notes so one transient healthcheck note is unobtrusive).
TEST_CONVERSATION = '047aa6e1-7bd3-431a-b8e7-0178849c1517'
PUBLIC_URL = 'https://mcp.constellation-memory.com/mcp'
HEALTH_MARK = '[PHASE0-HEALTHCHECK]'
SCRATCH_PORT = 8199          # never the production port
PRODUCTION_PORT = 8000
SERVER_BOOT_TIMEOUT = 90     # model preload can take a while on first spawn


def _tool_payload(result):
    """Normalize a fastmcp CallToolResult to plain Python data."""
    data = getattr(result, 'data', None)
    if data is not None:
        return data
    sc = getattr(result, 'structured_content', None)
    if sc is not None:
        return sc.get('result', sc) if isinstance(sc, dict) else sc
    content = getattr(result, 'content', None)
    if content:
        return json.loads(content[0].text)
    raise AssertionError(f'unrecognized tool result shape: {result!r}')


def _stdio_client():
    from fastmcp import Client
    from fastmcp.client.transports import PythonStdioTransport
    return Client(PythonStdioTransport(SERVER, python_cmd=VPY))


async def check_stdio_tools() -> set:
    """Check 1: stdio boot + exact tool inventory. Returns the tool-name set."""
    async with _stdio_client() as client:
        tools = {t.name for t in await client.list_tools()}
    missing = CANONICAL_TOOLS - tools
    extra = tools - CANONICAL_TOOLS
    if missing or extra:
        raise AssertionError(
            f'tool inventory mismatch — missing: {sorted(missing) or "none"}, '
            f'extra: {sorted(extra) or "none"}')
    return tools


async def check_round_trips() -> dict:
    """Check 3: get_stats sanity + notes add/read/delete round trip."""
    async with _stdio_client() as client:
        stats = _tool_payload(await client.call_tool('get_stats', {}))
        assert isinstance(stats.get('totalConversations'), int) and stats['totalConversations'] > 0, \
            f'get_stats totalConversations not sane: {stats!r}'
        assert stats.get('totalMessages', 0) > 0, f'get_stats totalMessages not sane: {stats!r}'
        lo, hi = stats['dateRange']
        assert len(lo) >= 10 and len(hi) >= 10 and lo <= hi, \
            f'get_stats dateRange not parseable: {stats["dateRange"]!r}'

        conv = _tool_payload(await client.call_tool(
            'get_conversation', {'conversation_id': TEST_CONVERSATION}))
        assert 'error' not in conv, f'test conversation missing from index: {conv!r}'
        start_count = len(conv.get('notes', []))

        note_id = None
        try:
            added = _tool_payload(await client.call_tool('add_conversation_note', {
                'conversation_id': TEST_CONVERSATION,
                'note_text': f'{HEALTH_MARK} transient note — auto-deleted by mcp_health',
            }))
            assert 'error' not in added, f'add_conversation_note failed: {added!r}'
            note_id = added['note']['note_id']

            conv2 = _tool_payload(await client.call_tool(
                'get_conversation', {'conversation_id': TEST_CONVERSATION}))
            texts = {n['note_id']: n['text'] for n in conv2.get('notes', [])}
            assert note_id in texts and HEALTH_MARK in texts[note_id], \
                'healthcheck note did not read back'
        finally:
            if note_id is not None:
                deleted = _tool_payload(await client.call_tool('delete_conversation_note', {
                    'conversation_id': TEST_CONVERSATION, 'note_id': note_id}))
                assert 'error' not in deleted, \
                    f'FAILED TO DELETE healthcheck note {note_id} — remove by hand: {deleted!r}'

        conv3 = _tool_payload(await client.call_tool(
            'get_conversation', {'conversation_id': TEST_CONVERSATION}))
        final_count = len(conv3.get('notes', []))
        assert final_count == start_count, \
            f'note count changed: start={start_count} final={final_count}'
        return stats


def _lsof_listeners(port: int) -> list:
    out = subprocess.run(['lsof', '-nP', f'-iTCP:{port}', '-sTCP:LISTEN'],
                         capture_output=True, text=True).stdout
    return [ln for ln in out.splitlines()[1:] if ln.strip()]


def assert_loopback_only(port: int):
    """Hard security assertion: listeners on `port` are 127.0.0.1 only."""
    lines = _lsof_listeners(port)
    assert lines, f'nothing listening on port {port}'
    for ln in lines:
        assert f'127.0.0.1:{port}' in ln, \
            f'INSECURE BINDING on port {port} (must be 127.0.0.1): {ln}'
        assert f'*:{port}' not in ln and f'0.0.0.0:{port}' not in ln, \
            f'INSECURE wildcard binding on port {port}: {ln}'


def check_http_binding(port: int = SCRATCH_PORT, host: str = '127.0.0.1') -> str:
    """Check 2: spawn HTTP-transport server, assert loopback-only binding.

    `host` is parameterized ONLY so the test suite can prove the check fails
    on 0.0.0.0 — production invocation never overrides it.
    """
    if _lsof_listeners(port):
        raise AssertionError(f'scratch port {port} already occupied — pick another')
    proc = subprocess.Popen(
        [VPY, SERVER, '--transport', 'http', '--port', str(port), '--host', host],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=str(REPO))
    try:
        deadline = time.time() + SERVER_BOOT_TIMEOUT
        while time.time() < deadline:
            if proc.poll() is not None:
                raise AssertionError(f'HTTP server exited early (rc={proc.returncode})')
            if _lsof_listeners(port):
                break
            time.sleep(0.5)
        else:
            raise AssertionError(f'HTTP server never bound port {port} in {SERVER_BOOT_TIMEOUT}s')
        assert_loopback_only(port)
        return '\n'.join(_lsof_listeners(port))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


async def check_divergence(local_stats: dict):
    """Check 4: compare public-tunnel fingerprint vs local. Returns
    (status, detail) where status is 'match' | 'DIVERGED' | 'unreachable'."""
    from fastmcp import Client
    from fastmcp.client.transports import StreamableHttpTransport
    def fingerprint(s):
        return (s.get('totalMessages'), (s.get('dateRange') or [None, None])[1])
    try:
        async with Client(StreamableHttpTransport(PUBLIC_URL)) as client:
            remote = _tool_payload(await asyncio.wait_for(
                client.call_tool('get_stats', {}), timeout=20))
    except Exception as e:
        return 'unreachable', f'{type(e).__name__}: {e}'
    lf, rf = fingerprint(local_stats), fingerprint(remote)
    if lf == rf:
        return 'match', f'fingerprint {lf}'
    return 'DIVERGED', (f'local (messages, max_date)={lf}  public={rf} — the tunnel '
                        f'is serving a DIFFERENT dataset/machine than the one under test')


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description='Constellation MCP health checks')
    ap.add_argument('--skip-network', action='store_true',
                    help='skip the public-tunnel divergence check')
    args = ap.parse_args(argv)

    fail = 0
    def ok(msg): print(f'  PASS {msg}')
    def bad(msg):
        nonlocal fail
        fail = 1
        print(f'  FAIL {msg}')
    def warn(msg): print(f'  WARN {msg}')

    print(f'== MCP health on {REPO} ==')
    try:
        tools = asyncio.run(check_stdio_tools())
        ok(f'stdio boot + tools/list: exactly {len(tools)} canonical tools')
    except Exception as e:
        bad(f'stdio boot: {e}')

    try:
        listing = check_http_binding()
        ok(f'http transport binds 127.0.0.1 only (scratch port {SCRATCH_PORT})')
    except Exception as e:
        bad(f'http binding: {e}')
    prod = _lsof_listeners(PRODUCTION_PORT)
    if prod:
        try:
            assert_loopback_only(PRODUCTION_PORT)
            ok(f'production port {PRODUCTION_PORT} listener is loopback-only')
        except AssertionError as e:
            bad(str(e))
    else:
        print(f'  INFO nothing on production port {PRODUCTION_PORT} (server not running — fine)')

    stats = None
    try:
        stats = asyncio.run(check_round_trips())
        ok(f'round trips: get_stats sane ({stats["totalConversations"]} conversations), '
           f'notes add/read/delete net-zero')
    except Exception as e:
        bad(f'round trips: {e}')

    if args.skip_network:
        print('  INFO divergence check skipped (--skip-network)')
    elif stats is None:
        warn('divergence check skipped: no local stats to compare')
    else:
        status, detail = asyncio.run(check_divergence(stats))
        if status == 'match':
            ok(f'public tunnel fingerprint matches local: {detail}')
        elif status == 'unreachable':
            print(f'  INFO public tunnel unreachable ({detail}) — divergence check skipped')
        else:
            warn(f'TWO-INSTANCE DIVERGENCE: {detail}')

    print(f'== mcp_health {"PASSED" if fail == 0 else "FAILED"} ==')
    return fail


if __name__ == '__main__':
    raise SystemExit(main())
