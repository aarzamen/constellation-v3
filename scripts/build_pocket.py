#!/usr/bin/env python3
"""
build_pocket.py — Constellation Pocket bundle exporter (Phase 1, schema v1).

Projects data/graph_data.json into a small, stamped, mobile-ready bundle:
    data/pocket/pocket_bundle.json      (raw, inlineable into Pocket.html)
    data/pocket/pocket_bundle.json.gz   (transport copy)

Design contract (Pocket v1 + Phase 2 Swift bridge):
  - Node ids pass through UNCHANGED (server-canonical strings; UUIDs for
    claude, provider-prefixed elsewhere e.g. "gemini_dcd6df585ab1").
    Semantic results arriving over the tunnel locate on the sky by
    id -> coords lookup in this bundle. Do not rewrite ids. Ever.
  - Edges reference node ARRAY INDICES as [src, dst, weight]; nodes[]
    order IS the index space and must never be re-sorted downstream.
  - Short node keys documented in header.keymap.
  - Cluster label/color/count live once in header.clusters, not per node.
  - No filesystem paths or manifest contents are embedded (bundle rides
    inside a phone app; keep host internals out of it).

Stdlib only. Standalone; safe to hook as a nightly post-step later.
Gates (exit 1 on any failure):
  G1 node count parity with source
  G2 spot-check id present with matching name
  G3 every kept edge endpoint is a valid node index
  G4 written bundle round-trips through json.loads
"""
import argparse
import gzip
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

SPOT_ID = "433481c7-eca8-4509-b597-e8e66c4db922"
SPOT_NAME = "Electric skateboard battery cell arrangement"

KEYMAP = {
    "id": "id", "n": "name", "p": "provider", "d": "date",
    "c": "cluster", "m": "messageCount", "t": "topTerms", "s": "snippet",
    "x": "x", "y": "y", "z": "z", "tx": "tx", "ty": "ty", "tz": "tz",
}


def clean(s, cap):
    """Collapse whitespace, strip, hard-cap length."""
    if not isinstance(s, str):
        return ""
    return re.sub(r"\s+", " ", s).strip()[:cap]


def fnum(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def main():
    ap = argparse.ArgumentParser(description="Build Constellation Pocket bundle")
    default_data = os.environ.get("CONSTELLATION_DATA_DIR") or str(
        Path(__file__).resolve().parent.parent / "data"
    )
    ap.add_argument("--data-dir", default=default_data)
    ap.add_argument("--out-dir", default=None,
                    help="default: <data-dir>/pocket")
    ap.add_argument("--edges-per-node", type=int, default=8,
                    help="keep an edge if it ranks in EITHER endpoint's top-K")
    ap.add_argument("--coord-decimals", type=int, default=2)
    ap.add_argument("--snippet-cap", type=int, default=160)
    ap.add_argument("--name-cap", type=int, default=120)
    args = ap.parse_args()

    data_dir = Path(args.data_dir)
    out_dir = Path(args.out_dir) if args.out_dir else data_dir / "pocket"
    gpath = data_dir / "graph_data.json"

    graph = json.loads(gpath.read_text(encoding="utf-8"))
    g_sha = hashlib.sha256(gpath.read_bytes()).hexdigest()
    g_mtime = datetime.fromtimestamp(
        gpath.stat().st_mtime, tz=timezone.utc
    ).isoformat(timespec="seconds")

    nodes_in = graph["nodes"]
    ndp = args.coord_decimals
    nd_idx = {}
    nodes_out = []
    for i, nd in enumerate(nodes_in):
        nd_idx[nd["id"]] = i
        nodes_out.append({
            "id": nd["id"],
            "n": clean(nd.get("name", ""), args.name_cap),
            "p": nd.get("provider", ""),
            "d": nd.get("date", ""),
            "c": nd.get("cluster", -1),
            "m": nd.get("messageCount", 0),
            "t": [clean(t, 24) for t in (nd.get("topTerms") or [])[:4]],
            "s": clean(nd.get("snippet", ""), args.snippet_cap),
            "x": round(fnum(nd.get("x")), ndp),
            "y": round(fnum(nd.get("y")), ndp),
            "z": round(fnum(nd.get("z")), ndp),
            "tx": round(fnum(nd.get("tx")), ndp),
            "ty": round(fnum(nd.get("ty")), ndp),
            "tz": round(fnum(nd.get("tz")), ndp),
        })

    # ---- edge thinning: union of each endpoint's top-K by weight ----
    edges_in = graph.get("edges", [])
    unresolved = 0
    canon = {}  # (lo, hi) -> max weight
    for e in edges_in:
        si = nd_idx.get(e.get("source"))
        ti = nd_idx.get(e.get("target"))
        if si is None or ti is None or si == ti:
            unresolved += 1
            continue
        key = (si, ti) if si < ti else (ti, si)
        w = fnum(e.get("weight"))
        prev = canon.get(key)
        if prev is None or w > prev:
            canon[key] = w

    per_node = [[] for _ in nodes_in]
    for (si, ti), w in canon.items():
        per_node[si].append((w, si, ti))
        per_node[ti].append((w, si, ti))

    keep = set()
    k = args.edges_per_node
    for lst in per_node:
        lst.sort(key=lambda x: -x[0])
        for _, si, ti in lst[:k]:
            keep.add((si, ti))
    edges_out = [[si, ti, round(canon[(si, ti)], 3)] for si, ti in sorted(keep)]

    clusters = graph.get("clusters", [])
    timeline = graph.get("timeline", [])
    stats = graph.get("stats", {})

    bundle = {
        "schema": 1,
        "kind": "constellation-pocket-bundle",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": {"graph_sha256": g_sha, "graph_mtime": g_mtime},
        "corpus": stats,
        "counts": {
            "nodes": len(nodes_out),
            "edges_in": len(edges_in),
            "edges_kept": len(edges_out),
            "edges_unresolved": unresolved,
            "clusters": len(clusters),
        },
        "keymap": KEYMAP,
        "edge_format": "[srcIndex, dstIndex, weight] indexing into nodes[]",
        "coord_decimals": ndp,
        "clusters": clusters,
        "timeline": timeline,
        "nodes": nodes_out,
        "edges": edges_out,
    }

    raw = json.dumps(bundle, ensure_ascii=False, separators=(",", ":"))
    out_dir.mkdir(parents=True, exist_ok=True)
    jpath = out_dir / "pocket_bundle.json"
    zpath = out_dir / "pocket_bundle.json.gz"
    jpath.write_text(raw, encoding="utf-8")
    zpath.write_bytes(gzip.compress(raw.encode("utf-8"), 9))
    b_sha = hashlib.sha256(raw.encode("utf-8")).hexdigest()

    # ---- gates ----
    fails = []
    if len(nodes_out) != len(nodes_in):
        fails.append(f"G1 node parity: {len(nodes_out)} != {len(nodes_in)}")
    spot = nodes_out[nd_idx[SPOT_ID]] if SPOT_ID in nd_idx else None
    if spot is None or spot["n"] != SPOT_NAME:
        got = spot["n"] if spot else None
        fails.append(f"G2 spot-check failed for {SPOT_ID}: got {got!r}")
    n = len(nodes_out)
    if any(not (0 <= s < n and 0 <= t < n) for s, t, _ in edges_out):
        fails.append("G3 edge index out of range")
    try:
        rt = json.loads(jpath.read_text(encoding="utf-8"))
        assert rt["counts"]["nodes"] == n
    except Exception as ex:  # noqa: BLE001 - gate reports anything
        fails.append(f"G4 round-trip failed: {ex}")

    print(f"pocket_bundle.json    {jpath.stat().st_size:>10,} bytes")
    print(f"pocket_bundle.json.gz {zpath.stat().st_size:>10,} bytes")
    print(f"sha256(raw) = {b_sha}")
    print(
        f"nodes={n} edges_in={len(edges_in)} edges_kept={len(edges_out)} "
        f"unresolved={unresolved} clusters={len(clusters)} K={k}"
    )
    if fails:
        for f in fails:
            print("GATE FAIL:", f)
        sys.exit(1)
    print("ALL GATES PASS")


if __name__ == "__main__":
    main()
