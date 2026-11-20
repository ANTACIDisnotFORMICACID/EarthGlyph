"""M1 real-index equivalence smoke (E-phase, docs/EXPERIMENTS.md reproducibility).

For three real, essentially closed query traces (perceptual-probe ph-* set,
source/licence recorded in each JSON; unmodified): search the REAL production
index (data/index, 839 candidates) three ways —

  1. legacy single-stroke ``{points, closed, unit}`` payload (old contract);
  2. schema-v2 with the same points split into 2 strokes;
  3. schema-v2 with the same points split into 4 strokes;

— via the loopback HTTP bridge (scripts/serve_sketch_search.py, frozen
production defaults). The v2 runs go through contour_assembly (stroke-boundary
junctions snap; the trace's own endpoints snap at the closing junction) and
then the identical production retrieval chain.

Open real traces (QuickDraw qd-*, rel gap 0.06-0.24) are deliberately NOT
split-tested here: their endpoint gaps exceed the multi-stroke junction
tolerance, so assembly must refuse them (verified separately, expected 400).

PASS: identical candidate_id sequence; align_distance within 5e-3 (junction
snaps perturb the ring by at most the endpoint tolerance; observed Top-10
neighbour distance gaps are >= 1e-2, so a 5e-3 band cannot flip a rank).

Run (server must be up):
  .venv/Scripts/python.exe scripts/serve_sketch_search.py --index data/index --port 8765
  .venv/Scripts/python.exe experiments/m1_equivalence_smoke.py
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

QUERIES = [
    "experiments/p0_perceptual_probe/queries/ph-lightning-fill.json",
    "experiments/p0_perceptual_probe/queries/ph-moon-fill.json",
    "experiments/p0_perceptual_probe/queries/ph-star-fill.json",
]
URL = "http://127.0.0.1:8765/search"
TOL = 5e-3


def search(payload: dict) -> dict:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        URL, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        raise SystemExit(f"HTTP {error.code}: {error.read().decode('utf-8')}") from error
    if not data.get("ok"):
        raise SystemExit(f"search refused: {data.get('error')}")
    return data


def split(points: list[list[float]], k: int) -> list[list[list[float]]]:
    """Contiguous split with shared boundary points (n % k == 0 not required)."""
    m = len(points)
    edges = sorted({i * m // k for i in range(1, k)})
    bounds = [0, *edges, m]
    return [points[bounds[i]:bounds[i + 1] + 1] for i in range(k)]


def main() -> int:
    failures = 0
    for path in QUERIES:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        name = Path(path).stem
        points = [[float(x), float(y)] for x, y in raw["points"]]
        legacy = search({"points": points, "closed": True, "unit": "canvas"})
        base_ids = [h["candidate_id"] for h in legacy["results"]]
        base_d = [h["align_distance"] for h in legacy["results"]]
        for k in (2, 4):
            v2 = {"schema_version": 2, "mode": "contour", "unit": "canvas",
                  "strokes": [{"points": s} for s in split(points, k)]}
            data = search(v2)
            ids = [h["candidate_id"] for h in data["results"]]
            ds = [h["align_distance"] for h in data["results"]]
            asm = data["assembly"]
            same = ids == base_ids
            maxdd = max((abs(a - b) for a, b in zip(ds, base_d)), default=0.0)
            close = len(ds) == len(base_d) and maxdd <= TOL
            print(f"{name} k={k}: status={asm['status']} "
                  f"junctions={len(asm['junctions'])} ids_match={same} max|dD|={maxdd:.4f}")
            if not same:
                failures += 1
                print(f"  FAIL ids: {ids[:3]}... vs {base_ids[:3]}...")
            if not close:
                failures += 1
                print("  FAIL align_distance band")
    print(f"\n{'PASS' if failures == 0 else f'FAIL ({failures})'} "
          f"({len(QUERIES)} real closed traces x {{2,4}}-stroke splits, index=data/index)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())