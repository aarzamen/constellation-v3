#!/usr/bin/env python3
"""
make_pocket.py — splice data/pocket/pocket_bundle.json into pocket/template.html
producing data/pocket/Pocket.html (single-file, offline Constellation Pocket).

Escaping: every '<' in the bundle JSON is rewritten as '\\u003c' (a legal JSON
escape) so the inlined payload can never contain '</script' or any other
sequence the HTML parser cares about. JSON.parse on the client restores it.

Gates (exit 1 on failure):
  G1 template contains the marker exactly once
  G2 output re-extracts and json-parses with matching node count
"""
import hashlib
import json
import os
import re
import sys
from pathlib import Path

MARK = "__BUNDLE_JSON__"


def main():
    repo = Path(__file__).resolve().parent.parent
    data = Path(os.environ.get("CONSTELLATION_DATA_DIR") or repo / "data")
    tpl_path = repo / "pocket" / "template.html"
    bundle_path = data / "pocket" / "pocket_bundle.json"
    out_path = data / "pocket" / "Pocket.html"

    tpl = tpl_path.read_text(encoding="utf-8")
    if tpl.count(MARK) != 1:
        print(f"GATE FAIL: marker count = {tpl.count(MARK)} (want 1)")
        sys.exit(1)

    raw = bundle_path.read_text(encoding="utf-8")
    safe = raw.replace("<", "\\u003c")          # literal replace; NOT regex
    out = tpl.replace(MARK, safe)
    out_path.write_text(out, encoding="utf-8")

    m = re.search(
        r'<script id="bundle" type="application/json">([^<]*)</script>', out
    )
    if not m:
        print("GATE FAIL: could not re-extract bundle from output")
        sys.exit(1)
    b = json.loads(m.group(1))
    if b["counts"]["nodes"] != len(b["nodes"]):
        print("GATE FAIL: node count mismatch after round-trip")
        sys.exit(1)

    sha = hashlib.sha256(out.encode("utf-8")).hexdigest()
    print(f"Pocket.html {out_path.stat().st_size:,} bytes")
    print(f"sha256 = {sha}")
    print(
        f"nodes={b['counts']['nodes']} edges={b['counts']['edges_kept']} "
        f"stamp={b['generated_at']} graph={b['source']['graph_sha256'][:8]}"
    )
    print("ALL GATES PASS")


if __name__ == "__main__":
    main()
