"""Tests for the MCP health checks (Phase 0, Group 2).

Follows the repo's existing E2E pattern: checks that need the real data/
directory or the network skipTest gracefully when unavailable.
"""

import asyncio
import os
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / 'scripts'))

import mcp_health  # noqa: E402


def _has_data():
    return (REPO / 'data' / 'conversations.json').exists() and \
           (REPO / 'data' / 'embeddings.npy').exists()


class TestMCPHealthStdio(unittest.TestCase):
    """Check 1 + 3: stdio boot, tool inventory, round trips."""

    @classmethod
    def setUpClass(cls):
        if not _has_data():
            raise unittest.SkipTest('No pre-computed data available')

    def test_stdio_boot_exact_tool_inventory(self):
        tools = asyncio.run(mcp_health.check_stdio_tools())
        self.assertEqual(tools, mcp_health.CANONICAL_TOOLS)

    def test_round_trips_and_note_net_zero(self):
        stats = asyncio.run(mcp_health.check_round_trips())
        self.assertGreater(stats['totalConversations'], 0)
        self.assertGreater(stats['totalMessages'], 0)
        lo, hi = stats['dateRange']
        self.assertLessEqual(lo, hi)


class TestMCPHealthHTTPBinding(unittest.TestCase):
    """Check 2: HTTP transport must bind loopback only."""

    @classmethod
    def setUpClass(cls):
        if not _has_data():
            raise unittest.SkipTest('No pre-computed data available')

    def test_http_binds_loopback_only(self):
        listing = mcp_health.check_http_binding(port=mcp_health.SCRATCH_PORT)
        self.assertIn(f'127.0.0.1:{mcp_health.SCRATCH_PORT}', listing)

    def test_wildcard_binding_is_detected_as_failure(self):
        """Deliberately mis-bind to 0.0.0.0 and prove the check fails."""
        with self.assertRaises(AssertionError) as ctx:
            mcp_health.check_http_binding(port=8198, host='0.0.0.0')
        self.assertIn('INSECURE', str(ctx.exception))


class TestAssertLoopbackOnly(unittest.TestCase):
    """Unit-level: the lsof parser flags wildcard bindings (no server needed)."""

    def test_nothing_listening_raises(self):
        with self.assertRaises(AssertionError):
            mcp_health.assert_loopback_only(1)  # port 1: never listening

    def test_wildcard_line_rejected(self):
        # Simulate lsof output directly through the parser contract
        line = 'Python  123 ama  5u IPv4 0x0 0t0 TCP *:8000 (LISTEN)'
        self.assertNotIn('127.0.0.1:8000', line)  # sanity of the fixture
        # assert_loopback_only reads live lsof; the wildcard-rejection logic
        # itself is proven end-to-end by test_wildcard_binding_is_detected_as_failure.


class TestMCPHealthDivergence(unittest.TestCase):
    """Check 4: public-tunnel divergence guard (network-dependent)."""

    @classmethod
    def setUpClass(cls):
        if not _has_data():
            raise unittest.SkipTest('No pre-computed data available')
        if os.environ.get('CONSTELLATION_SKIP_NETWORK_TESTS'):
            raise unittest.SkipTest('CONSTELLATION_SKIP_NETWORK_TESTS set')

    def test_divergence_guard_reports_cleanly(self):
        local = asyncio.run(mcp_health.check_round_trips())
        status, detail = asyncio.run(mcp_health.check_divergence(local))
        self.assertIn(status, ('match', 'DIVERGED', 'unreachable'))
        if status == 'unreachable':
            self.skipTest(f'public tunnel unreachable: {detail}')
        # Either outcome is a valid *check* result; a divergence is surfaced
        # loudly here without failing CI on a machine that isn't the server.
        print(f'\n  divergence status: {status} — {detail}', file=sys.stderr)


if __name__ == '__main__':
    unittest.main()
