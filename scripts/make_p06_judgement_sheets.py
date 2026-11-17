#!/usr/bin/env python
"""CLI: generate blind Top-10 judgement sheets for collected P0-6 human queries
(protocol §4). Requires real collected sessions — it refuses to run on an empty
sessions directory rather than fabricate anything.

For every declared-final query it runs the frozen production retriever ONCE
(same parameters as P0-5/P0-4 evaluations: fourier_align, top_k=10,
shortlist=200, smoothing=light, allow_mirror=False), renders the query contour
against the 10 returned candidates in a per-query randomized order labelled
A-J, and writes:

``judgement/sheets/<query_id>.png``   the blind sheet image (no ids, no ranks,
                                      no distances printed anywhere)
``judgement/sheet_judge_<NN>.csv``   one CSV per judge: every (query, option)
                                      pair with empty verdict columns, row
                                      order shuffled with that judge's own
                                      fixed seed (protocol §5)
``judgement/refusals.csv``            extraction refusals, verbatim reasons
``judgement/manifest.json``           run provenance (index commit, command)

The A-J -> candidate mapping goes to the gitignored local key file
``sessions/key_options.json``; it MUST NOT be committed or shown to judges.

The protocol §7 pre-flight gate (``scripts/validate_p06_dataset.py``) must
pass before any sheet is generated; ``--min-participants`` / ``--min-finals``
only lower the gate's thresholds for rehearsal runs, never its other checks.

Judge slots: ``--judges N`` produces one shuffled CSV per judge
(judge_01 ... judge_N, each with its own fixed seed), so independent judges
cannot copy each other's ordering.

Example
-------
python scripts/make_p06_judgement_sheets.py \
    --sessions-root experiments/p0_6_human_queries/sessions \
    --index data/index --judges 3
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import validate_p06_dataset  # noqa: E402

from earthglyph.geometry.sketch import SketchError  # noqa: E402
from earthglyph.retrieval.search import (  # noqa: E402
    SearchError,
    load_index_handle,
    search_by_contour,
)

OPTIONS = "ABCDEFGHIJ"


def sheet_figure(
    out_path: Path,
    query_points: np.ndarray,
    candidates: list[np.ndarray],
    title: str,
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(2, 6, figsize=(13.2, 4.6), squeeze=False)
    draw(axes[0][0], query_points, query=True)
    axes[0][0].set_title("query (the drawing)", fontsize=9)
    axes[0][1].axis("off")
    for slot, points in enumerate(candidates):
        ax = axes[(slot + 2) // 6][(slot + 2) % 6]
        draw(ax, points)
        ax.set_title(f"option {OPTIONS[slot]}", fontsize=9)
    fig.suptitle(title, fontsize=10)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    fig.savefig(out_path, dpi=130)
    plt.close(fig)


def draw(ax: Any, points: np.ndarray, *, query: bool = False) -> None:
    ring = np.vstack([points, points[:1]])
    color = "#1f77b4" if query else "#2ca02c"
    ax.plot(ring[:, 0], ring[:, 1], color=color, linewidth=1.2)
    ax.set_aspect("equal")
    ax.set_xticks([])
    ax.set_yticks([])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sessions-root", type=Path,
                        default=Path("experiments/p0_6_human_queries/sessions"))
    parser.add_argument("--index", type=Path, default=Path("data/index"))
    parser.add_argument("--out-dir", type=Path,
                        default=Path("experiments/p0_6_human_queries/judgement"))
    parser.add_argument("--judges", type=int, default=2)
    parser.add_argument("--seed", type=int, default=20261111)
    parser.add_argument("--min-participants", type=int, default=None,
                        help="lower the pre-flight gate's minimum participants "
                        "(rehearsal runs only; frozen default is 10)")
    parser.add_argument("--min-finals", type=int, default=None,
                        help="lower the pre-flight gate's minimum finals per "
                        "participant (rehearsal runs only; frozen default is 6)")
    args = parser.parse_args(argv)

    gate_argv = ["--sessions-root", str(args.sessions_root)]
    if args.min_participants is not None:
        gate_argv += ["--min-participants", str(args.min_participants)]
    if args.min_finals is not None:
        gate_argv += ["--min-finals", str(args.min_finals)]
    if validate_p06_dataset.main(gate_argv) != 0:
        raise SystemExit(
            "P0-6 pre-flight gate did not pass — judgement materials require a "
            "READY dataset (protocol §7); see the gate output above"
        )

    index = load_index_handle(args.index)
    sessions = sorted(args.sessions_root.glob("P*"))
    finals = [
        record
        for session in sessions
        for record in json.loads((session / "session.json").read_text(encoding="utf-8"))["files"]
        if record.get("status") == "registered" and record.get("final")
    ]
    if not finals:
        raise SystemExit(
            "no collected final queries found — P0-6 judgement sheets require real "
            "human sessions; the script will not fabricate data"
        )

    sheets_dir = args.out_dir / "sheets"
    sheets_dir.mkdir(parents=True, exist_ok=True)
    key_path = args.sessions_root / "key_options.json"
    option_key: dict[str, Any] = json.loads(key_path.read_text(encoding="utf-8")) \
        if key_path.is_file() else {}

    refusal_rows: list[list[str]] = []
    pairs: list[tuple[str, str]] = []
    manifest = {
        "generated_by": "scripts/make_p06_judgement_sheets.py",
        "index": index.provenance(),
        "params": {"method": "fourier_align", "top_k": 10, "shortlist": 200,
                   "smoothing": "light", "allow_mirror": False},
        "n_final_queries": len(finals),
        "judges": args.judges,
        "seed": args.seed,
        "gate": {"min_participants": args.min_participants,
                 "min_finals": args.min_finals},
        "queries": {},
    }
    for entry in finals:
        query_id = entry["query_id"]
        path = args.sessions_root / query_id[:3] / f"{query_id}.json"
        try:
            result = search_by_contour(path, index, top_k=10, method="fourier_align")
        except (SketchError, SearchError) as error:
            refusal_rows.append([query_id, str(error)])
            manifest["queries"][query_id] = {"refusal": str(error)}
            continue

        # seed = global seed * 10000 + participant number * 100 + query number;
        # query_id is "PXX-qNN", so the numbers live at [1:3] and [-2:]
        order_seed = args.seed * 10000 + int(query_id[1:3]) * 100 + int(query_id[-2:])
        rng = np.random.default_rng(order_seed)
        order = rng.permutation(len(result.hits))
        candidates = [np.asarray(result.hits[int(slot)].contour, dtype=float)
                      for slot in order]
        # option X must describe ONE candidate: row and candidate_id both come
        # from the same permuted hit, or judge verdicts cannot be traced back
        option_key[query_id] = {
            OPTIONS[slot]: {"row": int(result.hits[int(order[slot])].row),
                            "candidate_id": str(result.hits[int(order[slot])].candidate_id)}
            for slot in range(len(candidates))
        }
        contour = np.asarray(result.query["query_contour"]["points"], dtype=float)
        sheet_figure(sheets_dir / f"{query_id}.png", contour, candidates, query_id)
        manifest["queries"][query_id] = {
            "order_seed": int(order_seed),
            "top1_row": int(result.hits[0].row),
        }
        for slot in range(len(candidates)):
            pairs.append((query_id, OPTIONS[slot]))

    judge_seeds: dict[str, int] = {}
    for judge in range(1, args.judges + 1):
        label = f"judge_{judge:02d}"
        judge_seeds[label] = args.seed * 1000 + judge
        rows = list(pairs)
        np.random.default_rng(judge_seeds[label]).shuffle(rows)
        with (args.out_dir / f"sheet_{label}.csv").open(
            "w", newline="", encoding="utf-8"
        ) as handle:
            writer = csv.writer(handle)
            writer.writerow(["judge_id", "query_id", "option", "verdict",
                             "most_similar", "notes"])
            for row_query_id, option in rows:
                writer.writerow([label, row_query_id, option, "", "", ""])
    manifest["judge_seeds"] = judge_seeds

    key_path.write_text(json.dumps(option_key, indent=1, ensure_ascii=False), "utf-8")
    with (args.out_dir / "refusals.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["query_id", "refusal"])
        writer.writerows(refusal_rows)
    (args.out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=1, ensure_ascii=False), "utf-8"
    )
    print(f"wrote {len(manifest['queries'])} sheets / refusals, "
          f"{args.judges} shuffled judge sheet(s) x {len(pairs)} rows to {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
