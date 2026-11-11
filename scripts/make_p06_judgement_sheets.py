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
``judgement/sheet_<judge_slot>.csv``  one row per (query, option) with empty
                                      verdict columns for the judge to fill
``judgement/refusals.csv``            extraction refusals, verbatim reasons
``judgement/manifest.json``           run provenance (index commit, command)

The A-J -> candidate mapping goes to the gitignored local key file
``sessions/key_options.json``; it MUST NOT be committed or shown to judges.

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
    args = parser.parse_args(argv)

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
    sheet_rows: list[list[str]] = []
    manifest = {
        "generated_by": "scripts/make_p06_judgement_sheets.py",
        "index": index.provenance(),
        "params": {"method": "fourier_align", "top_k": 10, "shortlist": 200,
                   "smoothing": "light", "allow_mirror": False},
        "n_final_queries": len(finals),
        "judges": args.judges,
        "seed": args.seed,
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

        rng = np.random.default_rng(args.seed * 10000 + int(query_id[1:4]) * 100
                                    + int(query_id[-2:]))
        order = rng.permutation(len(result.hits))
        candidates = [np.asarray(result.hits[int(slot)].contour, dtype=float)
                      for slot in order]
        rows_map = [int(result.hits[int(slot)].row) for slot in order]
        key_path_by_query = f"{query_id}"
        option_key[key_path_by_query] = {
            OPTIONS[slot]: {"row": rows_map[slot],
                            "candidate_id": str(result.hits[int(slot)].candidate_id)}
            for slot in range(len(candidates))
        }
        contour = np.asarray(result.query["query_contour"]["points"], dtype=float)
        sheet_figure(sheets_dir / f"{query_id}.png", contour, candidates, query_id)
        manifest["queries"][query_id] = {
            "order_seed": int(args.seed * 10000 + int(query_id[1:4]) * 100
                              + int(query_id[-2:])),
            "top1_row": int(result.hits[0].row),
        }
        for judge in range(1, args.judges + 1):
            for slot in range(len(candidates)):
                sheet_rows.append([f"judge_{judge:02d}", query_id, OPTIONS[slot],
                                   "", "", ""])

    key_path.write_text(json.dumps(option_key, indent=1, ensure_ascii=False), "utf-8")
    args.out_dir.mkdir(parents=True, exist_ok=True)
    with (args.out_dir / "judgement_sheet_blank.csv").open(
        "w", newline="", encoding="utf-8"
    ) as handle:
        writer = csv.writer(handle)
        writer.writerow(["judge_id", "query_id", "option", "verdict",
                         "most_similar", "notes"])
        writer.writerows(sheet_rows)
    with (args.out_dir / "refusals.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["query_id", "refusal"])
        writer.writerows(refusal_rows)
    (args.out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=1, ensure_ascii=False), "utf-8"
    )
    print(f"wrote {len(manifest['queries'])} sheets / refusals, "
          f"{len(sheet_rows)} empty judgement rows to {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
