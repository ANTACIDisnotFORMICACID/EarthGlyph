#!/usr/bin/env python
"""Checkpoint D1 — make the four arms comparable *by eye*, then hide which is which.

Checkpoint C answered "which arm ranks the frozen labels highest".  It could not answer
"which arm returns the shape a person would call the same shape", for two reasons this
script removes:

**The pictures were not comparable.**  E0 returns a rotation and a cyclic shift per
candidate, so ``visualize_perceptual.py`` drew E0's candidates already fitted onto the
query and drew E1/E2/E3's as-returned (see that file's ``_compare_panel``).  A reader
judging "which ring overlaps the query better" was reading E0's own objective off E0's
panel and nothing off the others.  So every figure here comes in two registered views:
``unaligned`` (the stored contour, no transform from anybody) and ``display_aligned``
(the stored contour rotated by :mod:`earthglyph.retrieval.display_alignment`, one rule
for all four arms, arms excluded).  The alignment is applied to the *drawing* after the
ranking was read out of ``results.csv``; it moves no score and no rank, and each page
says so.

**The pictures were not blind.**  Panels were captioned with the arm, the distance and
the label, so an eye-check could not help but confirm a expectation.  Here each
(query, rank) group is one row: the query, then the four candidates the four arms put at
that rank, permuted into letters A-D by a seeded shuffle whose key lives in a separate
file.  Distances, ``REL`` flags, families, roles, provenance and arm names are all off
the page; the candidate keeps an opaque ``C####`` code instead of its ``item_id``
precisely because ids such as ``syn-heart-e3`` whisper the family and the role.

Run (a few minutes; the display fits dominate):

    python scripts/blind_perceptual_review.py --settings T2,T3 --top 5

Scores are not produced here.  ``visual_review_template.csv`` ships empty on purpose —
filling it is Task B, and a template that arrived pre-scored would be the thing this
whole checkpoint exists to avoid.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from evaluate_perceptual_methods import (  # noqa: E402
    GALLERY_LABEL,
    INDEX_LABEL,
    load_gallery,
    load_landforms,
)
from visualize_retrieval import _figure_backend  # noqa: E402  (selects the Agg backend)

ARMS = ("E0", "E1", "E2", "E3")
LETTERS = ("A", "B", "C", "D")
LABELLED_VIEWS = ("unaligned", "display_aligned")

# One palette for every candidate panel, in both views and both settings.  Any
# per-arm styling would re-open the unfairness the blinding is meant to close.
CANDIDATE_FACE = "#cfd9e6"
CANDIDATE_EDGE = "#2f3b4c"
QUERY_FACE = "#f6dede"
QUERY_EDGE = "#a4262c"
OVERLAY_EDGE = "#12623c"
LINE_W = 0.9


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _wrap(text: str, width: int = 132) -> str:
    """Hard-wrap a caption so nothing runs off the page.

    ``fig.text`` does not wrap, and the two sentences that have to be true on every
    sheet — the rubric and the "this transform is display-only" disclaimer — are the
    longest strings in the build.
    """
    from textwrap import fill

    return "\n".join(fill(line, width=width) for line in text.split("\n"))


def _chars_per_line(fig_width_in: float, fontsize: float) -> int:
    """How many characters fit across a page at this point size.

    The estimate is one number — an average glyph is about 0.55 em — because the
    alternative is letting a two-line title run off the right edge of a wide sheet,
    which is what it did before this helper existed.
    """
    return max(40, int(fig_width_in / (0.0076 * fontsize)))


def _reverse(codes: dict[str, str]) -> dict[str, str]:
    """``id -> code`` flipped to ``code -> id``, ordered by code for readability."""
    return {code: item_id for item_id, code in sorted(codes.items(), key=lambda kv: kv[1])}


def git_commit() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True, timeout=20
        )
    except Exception:  # noqa: BLE001 - provenance is best effort, never fatal
        return None
    return result.stdout.strip() or None


def load_results(path: Path) -> dict[tuple[str, str, str], list[dict[str, Any]]]:
    """``(setting, arm, query_id) -> rows in rank order`` straight from results.csv.

    Same rule as Checkpoint C's figures: read the artefact the metrics were computed
    from, so a picture cannot disagree with a table printed beside it.
    """
    cells: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            cells.setdefault((row["setting"], row["arm"], row["query_id"]), []).append(row)
    for rows in cells.values():
        rows.sort(key=lambda row: int(row["rank"]))
    return cells


class Contours:
    """Stored 128-point rings by id, plus the one axis frame every panel shares."""

    def __init__(self) -> None:
        self._rings: dict[str, np.ndarray] = {}
        self._needed: set[str] = set()

    def register(self, item_id: str, ring: np.ndarray) -> None:
        self._rings[item_id] = np.asarray(ring, dtype=float)

    def need(self, item_id: str) -> None:
        if item_id not in self._rings:
            self._needed.add(item_id)

    def __getitem__(self, item_id: str) -> np.ndarray:
        return self._rings[item_id]

    def __contains__(self, item_id: str) -> bool:
        return item_id in self._rings

    def frame_for(self, item_ids: set[str]) -> float:
        """Half-width of the shared square frame, from the shapes actually drawn.

        Fixed per setting rather than per panel: "same size, same coordinate scale,
        same margin" is the whole point, and a per-panel autoscale would quietly make a
        compact shape fill its cell and a spindly one shrink.  A shape with a long arm
        can reach twice the RMS radius of a round one, so the frame is the union.
        """
        missing = sorted(self._needed - set(self._rings))
        if missing:
            raise SystemExit(f"contours never loaded for {missing[:5]}")
        peaks = [float(np.abs(self._rings[item_id]).max()) for item_id in sorted(item_ids)]
        if not peaks:
            raise SystemExit("empty display set: nothing to frame")
        return round(max(peaks) * 1.08, 3)


def draw_panel(
    ax: Any,
    ring: np.ndarray,
    *,
    frame: float,
    face: str,
    edge: str,
    caption: str,
    caption_colour: str,
    overlay: np.ndarray | None = None,
    fontsize: float = 5.6,
) -> None:
    """The only panel primitive here: filled ring, square frame, equal aspect, caption.

    No markers, no per-arm line styles, no autoscaling — three hundred of these have to
    be indistinguishable in rendering while the shapes inside them differ.
    """
    closed = np.vstack([ring, ring[:1]])
    ax.fill(closed[:, 0], closed[:, 1], color=face, zorder=1)
    ax.plot(closed[:, 0], closed[:, 1], color=edge, lw=LINE_W, zorder=2)
    if overlay is not None:
        ring_closed = np.vstack([overlay, overlay[:1]])
        ax.plot(
            ring_closed[:, 0],
            ring_closed[:, 1],
            color=OVERLAY_EDGE,
            lw=LINE_W,
            linestyle="--",
            zorder=3,
        )
    ax.set_aspect("equal")
    ax.set_xlim(-frame, frame)
    ax.set_ylim(-frame, frame)
    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_color("#b6bfcc")
        spine.set_linewidth(0.5)
    ax.set_title(caption, fontsize=fontsize, color=caption_colour, pad=1.6)


def pose_cache_path(out_dir: Path) -> Path:
    return out_dir / "display_poses.json"


class PoseFitter:
    """Rotation-only display fit, memoised to disk so a re-run does not re-sweep."""

    def __init__(self, cache_file: Path | None) -> None:
        self._fits: dict[str, dict[str, Any]] = {}
        self._dirty = False
        self._cache_file = cache_file
        if cache_file is not None and cache_file.exists():
            self._fits = json.loads(cache_file.read_text(encoding="utf-8"))

    def pose(
        self, key: str, query: np.ndarray, candidate: np.ndarray
    ) -> tuple[np.ndarray, dict[str, Any]]:
        from earthglyph.retrieval.display_alignment import align_for_display

        known = self._fits.get(key)
        if known is not None and known.get("contour_sha") == _pair_sha(query, candidate):
            return candidate @ _rot(float(known["theta_deg"])).T, known
        posed, info = align_for_display(query, candidate)
        info = {**info, "contour_sha": _pair_sha(query, candidate)}
        self._fits[key] = info
        self._dirty = True
        return posed, info

    def save(self) -> dict[str, int]:
        if self._cache_file is None:
            return {"entries": len(self._fits), "written": 0}
        if self._dirty:
            self._cache_file.write_text(
                json.dumps(self._fits, indent=0, sort_keys=True) + "\n", encoding="utf-8"
            )
        return {"entries": len(self._fits), "written": int(self._dirty)}


def _pair_sha(left: np.ndarray, right: np.ndarray) -> str:
    return hashlib.sha256(
        np.ascontiguousarray(left).tobytes() + np.ascontiguousarray(right).tobytes()
    ).hexdigest()[:20]


def _rot(angle_deg: float) -> np.ndarray:
    theta = np.radians(angle_deg)
    return np.array([[np.cos(theta), -np.sin(theta)], [np.sin(theta), np.cos(theta)]])


def build_cells(
    setting: str,
    query_id: str,
    cells: dict[tuple[str, str, str], list[dict[str, Any]]],
    contours: Contours,
    *,
    top: int,
) -> list[dict[str, Any]]:
    """One record per (query, rank, arm) actually returned, with nothing invented.

    ``results.csv`` only stores the Top-10 plus every labelled row, so ``rank <= top`` is
    always present.  A cell is skipped — never padded — if an arm returned no candidate at
    that rank, and the count is reported so a short row cannot be mistaken for a bad arm.
    """
    out: list[dict[str, Any]] = []
    for rank in range(1, top + 1):
        for arm in ARMS:
            rows = cells.get((setting, arm, query_id), [])
            if rank > len(rows):
                continue
            row = rows[rank - 1]
            if int(row["rank"]) != rank:
                raise SystemExit(
                    f"{setting}/{arm}/{query_id}: row labelled rank {row['rank']} sat at "
                    f"position {rank}; results.csv is not in rank order"
                )
            candidate_id = row["candidate_id"]
            if candidate_id not in contours:
                raise SystemExit(f"{setting}/{arm}/{query_id}: no contour for {candidate_id}")
            out.append(
                {
                    "setting": setting,
                    "query_id": query_id,
                    "rank": rank,
                    "arm": arm,
                    "candidate_id": candidate_id,
                }
            )
    return out


def blind_cells(
    records: list[dict[str, Any]], *, seed: int
) -> dict[tuple[str, str, int], dict[str, str]]:
    """Attach a per-group seeded letter assignment; the key goes to a separate file.

    The shuffle is per (query, rank), not per query, so no column position can carry
    information: a reviewer who notices "D is always the shape-context answer" has
    noticed a run of luck, and the next row's permutation is independent of it.
    """
    rng = np.random.default_rng(seed)
    groups: dict[tuple[str, str, int], list[dict[str, Any]]] = {}
    for record in records:
        groups.setdefault((record["setting"], record["query_id"], record["rank"]), []).append(
            record
        )
    assignments: dict[tuple[str, str, int], dict[str, str]] = {}
    for key in sorted(groups):
        members = sorted(groups[key], key=lambda record: record["arm"])
        if len(members) > len(LETTERS):
            raise SystemExit(f"more arms ({len(members)}) than blind letters")
        permutation = rng.permutation(len(members))
        shuffled = [members[int(index)] for index in permutation]
        for letter, record in zip(LETTERS, shuffled, strict=False):
            record["blind_label"] = letter
        assignments[key] = {
            letter: record["arm"] for letter, record in zip(LETTERS, shuffled, strict=False)
        }
    return assignments


def make_codes(records: list[dict[str, Any]]) -> tuple[dict[str, str], dict[str, str]]:
    """Opaque query and candidate codes, assigned on sorted ids so they are stable.

    ``item_id`` is deliberately not shown: a gallery id encodes family and role
    (``syn-heart-e3`` says "heart", ``qd-star-variant-02`` says "labelled relevant"),
    which is exactly the information a blind sheet must withhold.
    """
    query_codes = {
        query_id: f"Q{index:02d}"
        for index, query_id in enumerate(sorted({r["query_id"] for r in records}), start=1)
    }
    candidate_codes = {
        candidate_id: f"C{index:04d}"
        for index, candidate_id in enumerate(
            sorted({r["candidate_id"] for r in records}), start=1
        )
    }
    return query_codes, candidate_codes


def fair_figure(
    plt: Any,
    family: str,
    query_ids: list[str],
    records: list[dict[str, Any]],
    contours: Contours,
    fitter: PoseFitter,
    out_path: Path,
    *,
    setting: str,
    frame: float,
    top: int,
    dpi: int,
) -> dict[str, Any]:
    """A2's two views, side by side, still labelled — the fairness evidence itself.

    One row per (query, arm); the left block is the arm's Top-k drawn as stored, the
    right block is the *same* candidates drawn after the shared display fit.  Putting
    them in one row is the point: the reader can see, for the same arm and the same
    candidate, that the aligned block changes legibility and not preference.
    """
    columns = 1 + 2 * top
    rows_spec = [(query_id, arm) for query_id in query_ids for arm in ARMS]
    fig_w = 0.74 * columns + 2.1
    fig_h = 0.86 * len(rows_spec) + 2.0
    wrap = _chars_per_line(fig_w, 5.8)
    title_wrap = _chars_per_line(fig_w, 8.6)
    fig, axes = plt.subplots(
        len(rows_spec), columns, figsize=(fig_w, fig_h), squeeze=False, dpi=dpi
    )
    for index, (query_id, arm) in enumerate(rows_spec):
        query = contours[query_id]
        draw_panel(
            axes[index][0],
            query,
            frame=frame,
            face=QUERY_FACE,
            edge=QUERY_EDGE,
            caption=f"QUERY {query_id}",
            caption_colour=QUERY_EDGE,
            fontsize=4.6,
        )
        rows = sorted(
            (r for r in records if r["query_id"] == query_id and r["arm"] == arm),
            key=lambda r: r["rank"],
        )
        for view_offset, view in enumerate(LABELLED_VIEWS):
            for slot in range(top):
                ax = axes[index][1 + view_offset * top + slot]
                if slot >= len(rows):
                    ax.axis("off")
                    continue
                record = rows[slot]
                candidate = contours[record["candidate_id"]]
                overlay: np.ndarray | None = None
                shown = candidate
                if view == "display_aligned":
                    shown, _ = fitter.pose(
                        f"{setting}|{query_id}|{record['candidate_id']}", query, candidate
                    )
                    overlay = query
                draw_panel(
                    ax,
                    shown,
                    frame=frame,
                    face=CANDIDATE_FACE,
                    edge=CANDIDATE_EDGE,
                    caption=f"{record['rank']} {record['candidate_id']}",
                    caption_colour=CANDIDATE_EDGE,
                    overlay=overlay,
                    fontsize=4.3,
                )
        axes[index][0].set_ylabel(
            f"{arm}\n{family}", fontsize=5.6, rotation=0, ha="right", va="center",
            labelpad=26, color="#1b2735",
        )
    fig.suptitle(
        _wrap(
            f"Fair display comparison · setting {setting} · family '{family}' · "
            f"{GALLERY_LABEL if setting != 'T3' else INDEX_LABEL}",
            width=title_wrap,
        )
        + "\n"
        + _wrap(
            f"one row per arm · left block Top-{top} as stored · right block the same "
            f"{top} candidates after one shared display fit",
            width=title_wrap,
        ),
        fontsize=8.6,
        x=0.012,
        ha="left",
        y=1.0 - 0.045 / fig_h,
        va="top",
        linespacing=1.7,
    )
    fig.text(
        0.012,
        0.014,
        _wrap(
            "display_aligned = rotated by earthglyph.retrieval.display_alignment "
            "(symmetric chamfer, rotation only), which is NOT one of E0..E3 and is applied to "
            "all four arms identically.\n"
            "the fit changes the drawing only: scores and ranks are read from results.csv and "
            "were fixed before it ran.  dashed green ring = the query, in the same frame.\n"
            f"every panel in this figure shares the square frame +-{frame} and one palette; "
            "no candidate was re-sampled, smoothed or resized.",
            width=wrap,
        ),
        fontsize=5.8,
        color="#40506a",
        linespacing=1.5,
    )
    fig.subplots_adjust(
        left=0.075, right=0.99, top=1.0 - 1.02 / fig_h, bottom=1.02 / fig_h,
        wspace=0.1, hspace=0.42,
    )
    fig.savefig(out_path, dpi=dpi)
    plt.close(fig)
    return {
        "path": str(out_path),
        "family": family,
        "setting": setting,
        "views": list(LABELLED_VIEWS),
        "rows": len(rows_spec),
        "panels": len(rows_spec) * columns,
    }


def blind_figure(
    plt: Any,
    groups: list[dict[str, Any]],
    contours: Contours,
    fitter: PoseFitter,
    candidate_codes: dict[str, str],
    query_codes: dict[str, str],
    out_path: Path,
    *,
    setting: str,
    view: str,
    frame: float,
    title: str,
    dpi: int,
    columns_of_groups: int = 1,
) -> dict[str, Any]:
    """A3's blind sheet: one row per (query, rank), four unnamed candidates in A-D order.

    What is on the page: the query's code, the rank, and four letters.  What is not:
    arm, distance, relevance label, family, role, provenance, algorithm ordering.  Two
    letters can hold the same shape — that is a true fact about two arms, not a leak.
    """
    per_group = 1 + len(LETTERS)
    columns = per_group * columns_of_groups
    if columns_of_groups < 1:
        raise SystemExit("--blind-columns must be at least 1")
    # the sheet is laid out in vertical bands of `columns_of_groups` groups, so a 90-group
    # overview is 30 rows of 15 panels rather than a 90-row scroll nobody can read
    rows = -(-len(groups) // columns_of_groups)
    fig_w = 0.72 * columns + 1.6
    fig_h = 0.86 * rows + 1.9
    wrap = _chars_per_line(fig_w, 5.8)
    title_wrap = _chars_per_line(fig_w, 8.6)
    fig, axes = plt.subplots(rows, columns, figsize=(fig_w, fig_h), squeeze=False, dpi=dpi)
    filled: set[tuple[int, int]] = set()
    for index, group in enumerate(groups):
        block, row = divmod(index, rows)
        base = block * per_group
        query = contours[group["query_id"]]
        filled.add((row, base))
        draw_panel(
            axes[row][base],
            query,
            frame=frame,
            face=QUERY_FACE,
            edge=QUERY_EDGE,
            caption=f"{query_codes[group['query_id']]}\nrank {group['rank']}",
            caption_colour=QUERY_EDGE,
            fontsize=5.4,
        )
        for offset, letter in enumerate(LETTERS):
            ax = axes[row][base + 1 + offset]
            filled.add((row, base + 1 + offset))
            record = group["by_label"].get(letter)
            if record is None:
                ax.axis("off")
                continue
            candidate = contours[record["candidate_id"]]
            overlay: np.ndarray | None = None
            shown = candidate
            if view == "display_aligned":
                shown, _ = fitter.pose(
                    f"{setting}|{group['query_id']}|{record['candidate_id']}", query, candidate
                )
                overlay = query
            draw_panel(
                ax,
                shown,
                frame=frame,
                face=CANDIDATE_FACE,
                edge=CANDIDATE_EDGE,
                caption=letter,
                caption_colour=CANDIDATE_EDGE,
                overlay=overlay,
                fontsize=6.6,
            )
    # a partial last band leaves real axes with nothing in them; left alone they draw a
    # frame and a tick, which reads as "this group had no candidate" rather than "no group"
    for row in range(rows):
        for column in range(columns):
            if (row, column) not in filled:
                axes[row][column].axis("off")
    fig.suptitle(
        _wrap(
            f"BLIND REVIEW SHEET · setting {setting} · view {view}", width=title_wrap
        )
        + "\n"
        + _wrap(
            f"{title} · arm, distance, label, family and provenance are withheld",
            width=title_wrap,
        ),
        fontsize=8.6,
        x=0.012,
        ha="left",
        y=1.0 - 0.045 / fig_h,
        va="top",
        linespacing=1.7,
    )
    notes = [
        "score each letter 0-3 against the query in its own row: 0 clearly different / "
        "missing key structure, 1 partly similar but overall different, 2 overall similar "
        "with the key structure kept, 3 very similar apart from plausible local deformation.",
        "do not give marks for a corner count: a five-pointed star and a five-lobed fork "
        "have five points each and are not the same structure.",
    ]
    if view == "display_aligned":
        notes.append(
            "display_aligned: every candidate got the same rotation-only display fit, which "
            "belongs to no arm and changed no score; the dashed ring is the row's query."
        )
    else:
        notes.append(
            "unaligned: shapes appear exactly as the standardizer stored them, so rotate "
            "them mentally before judging; orientation is not part of the score."
        )
    notes.append(
        "the same shape can appear under two letters when two arms returned it; that is a "
        "fact about the arms, not a printing error."
    )
    fig.text(
        0.012,
        0.014,
        _wrap("\n".join(notes), width=wrap),
        fontsize=5.8,
        color="#40506a",
        linespacing=1.5,
    )
    fig.subplots_adjust(
        left=0.012, right=0.992, top=1.0 - 1.06 / fig_h, bottom=1.16 / fig_h,
        wspace=0.09, hspace=0.36,
    )
    fig.savefig(out_path, dpi=dpi)
    plt.close(fig)
    return {
        "path": str(out_path),
        "setting": setting,
        "view": view,
        "groups": len(groups),
        "rows": rows,
        "panels": len(groups) * per_group,
        "frame": frame,
    }


def write_template(path: Path, groups: list[dict[str, Any]], query_codes: dict[str, str]) -> int:
    """The answer sheet, empty.  One row per blinded cell, both views, both settings."""
    columns = [
        "setting",
        "view",
        "query_code",
        "rank",
        "blind_label",
        "candidate_code",
        "score_0_3",
        "key_structure_preserved",
        "obvious_error",
        "low_confidence",
        "reason",
        "reviewer",
        "status",
    ]
    written = 0
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for group in groups:
            for view in LABELLED_VIEWS:
                for letter in LETTERS:
                    record = group["by_label"].get(letter)
                    if record is None:
                        continue
                    writer.writerow(
                        {
                            "setting": group["setting"],
                            "view": view,
                            "query_code": query_codes[group["query_id"]],
                            "rank": group["rank"],
                            "blind_label": letter,
                            "candidate_code": record["blind_candidate_code"],
                            "score_0_3": "",
                            "key_structure_preserved": "",
                            "obvious_error": "",
                            "low_confidence": "",
                            "reason": "",
                            "reviewer": "",
                            "status": "pending_review",
                        }
                    )
                    written += 1
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--gallery-dir", type=Path, default=Path("experiments/p0_perceptual_probe")
    )
    parser.add_argument("--index", type=Path, default=Path("data/index"))
    parser.add_argument(
        "--out-dir", type=Path, default=Path("experiments/p0_perceptual_probe/checkpoint_d")
    )
    parser.add_argument("--results", type=Path, default=None)
    parser.add_argument("--settings", default="T2,T3")
    parser.add_argument("--top", type=int, default=5)
    parser.add_argument(
        "--fair-top",
        type=int,
        default=3,
        help="Top-k drawn in the labelled fair figures; the blind sheets always carry --top",
    )
    parser.add_argument(
        "--fair-settings",
        default="T2",
        help=(
            "settings that get the labelled fair figures.  T2 only by default: the fair "
            "comparison exists to prove the display rule is applied evenly, and T2 is where "
            "the evaluation happens; drawing it again over 839 landforms triples the bytes "
            "for no new evidence, and T3 keeps both views in its blind pages instead"
        ),
    )
    parser.add_argument("--dpi", type=int, default=170, help="resolution of the blind pages")
    parser.add_argument(
        "--fair-dpi", type=int, default=150, help="resolution of the labelled fair figures"
    )
    parser.add_argument(
        "--unaligned-dpi",
        type=int,
        default=130,
        help="resolution of the unaligned reference pages (a cross-check, not the scored view)",
    )
    parser.add_argument(
        "--overview-dpi", type=int, default=120, help="resolution of the contact-sheet overview"
    )
    parser.add_argument("--seed", type=int, default=20261107)
    parser.add_argument("--families", default="all")
    parser.add_argument(
        "--figures",
        default="fair,blind",
        help="comma list of fair,blind,blind_unaligned to limit what is drawn",
    )
    parser.add_argument(
        "--blind-columns",
        type=int,
        default=3,
        help="how many (query, rank) groups sit side by side on every blind sheet",
    )
    parser.add_argument(
        "--no-pose-cache",
        action="store_true",
        help="ignore display_poses.json and re-sweep every pair (for timing)",
    )
    args = parser.parse_args(argv)

    settings = [item for item in args.settings.split(",") if item]
    if any(setting not in ("T1", "T2", "T3") for setting in settings):
        raise SystemExit("--settings must be a subset of T1,T2,T3")
    fair_settings = [item for item in args.fair_settings.split(",") if item]
    if any(setting not in settings for setting in fair_settings):
        raise SystemExit("--fair-settings must be a subset of --settings")
    kinds = [item for item in args.figures.split(",") if item]
    if any(kind not in ("fair", "blind", "blind_unaligned") for kind in kinds):
        raise SystemExit("--figures must be a subset of fair,blind,blind_unaligned")

    plt = _figure_backend()
    from visualize_perceptual import configure_cjk_font

    configure_cjk_font()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    results_path = args.results or (args.gallery_dir / "results.csv")
    cells = load_results(results_path)
    gallery = load_gallery(args.gallery_dir)
    labels = json.loads((args.gallery_dir / "labels.json").read_text(encoding="utf-8"))

    contours = Contours()
    for item_id, item in gallery.items():
        contours.register(item_id, item.contour)
    if "T3" in settings:
        landform_ids, landform_rows, _ = load_landforms(args.index)
        for item_id, ring in zip(landform_ids, landform_rows, strict=True):
            contours.register(item_id, ring)

    families = sorted({entry["family"] for entry in labels["queries"].values()})
    if args.families != "all":
        families = [family for family in families if family in args.families.split(",")]

    fitter = PoseFitter(None if args.no_pose_cache else pose_cache_path(args.out_dir))
    started = time.perf_counter()

    records: list[dict[str, Any]] = []
    frames: dict[str, float] = {}
    for setting in settings:
        for query_id in sorted(labels["queries"]):
            if labels["queries"][query_id]["family"] not in families:
                continue
            records.extend(
                build_cells(setting, query_id, cells, contours, top=args.top)
            )
        frames[setting] = contours.frame_for(
            {record["candidate_id"] for record in records if record["setting"] == setting}
            | {record["query_id"] for record in records if record["setting"] == setting}
        )
    build_seconds = time.perf_counter() - started

    assignments = blind_cells(records, seed=args.seed)
    query_codes, candidate_codes = make_codes(records)
    by_cell: dict[tuple[str, str, int, str], dict[str, Any]] = {
        (r["setting"], r["query_id"], r["rank"], r["blind_label"]): r for r in records
    }
    groups: dict[tuple[str, str, int], dict[str, Any]] = {}
    for key in sorted(assignments):
        setting, query_id, rank = key
        groups[key] = {
            "setting": setting,
            "query_id": query_id,
            "rank": rank,
            "family": labels["queries"][query_id]["family"],
            "by_label": {
                letter: by_cell[(setting, query_id, rank, letter)]
                for letter in LETTERS
                if (setting, query_id, rank, letter) in by_cell
            },
        }
    for group in groups.values():
        for record in group["by_label"].values():
            record["blind_candidate_code"] = candidate_codes[record["candidate_id"]]

    produced: list[Path] = []
    figure_log: list[dict[str, Any]] = []
    for setting in settings:
        setting_groups = [group for group in groups.values() if group["setting"] == setting]
        frame = frames[setting]
        if "fair" in kinds and setting in fair_settings:
            for family in families:
                query_ids = sorted(
                    {group["query_id"] for group in setting_groups if group["family"] == family}
                )
                if not query_ids:
                    continue
                out_path = args.out_dir / f"fair_{family}_{setting.lower()}.png"
                figure_log.append(
                    fair_figure(
                        plt,
                        family,
                        query_ids,
                        [r for r in records if r["setting"] == setting],
                        contours,
                        fitter,
                        out_path,
                        setting=setting,
                        frame=frame,
                        top=args.fair_top,
                        dpi=args.fair_dpi,
                    )
                )
                produced.append(out_path)
        if "blind" in kinds:
            for family in families:
                subset = [g for g in setting_groups if g["family"] == family]
                if not subset:
                    continue
                out_path = args.out_dir / f"blind_{family}_{setting.lower()}.png"
                figure_log.append(
                    blind_figure(
                        plt,
                        subset,
                        contours,
                        fitter,
                        candidate_codes,
                        query_codes,
                        out_path,
                        setting=setting,
                        view="display_aligned",
                        frame=frame,
                        title=f"family '{family}' · {len(subset)} blinded groups",
                        dpi=args.dpi,
                        columns_of_groups=args.blind_columns,
                    )
                )
                produced.append(out_path)
            out_path = args.out_dir / f"blind_review_{setting.lower()}.png"
            figure_log.append(
                blind_figure(
                    plt,
                    setting_groups,
                    contours,
                    fitter,
                    candidate_codes,
                    query_codes,
                    out_path,
                    setting=setting,
                    view="display_aligned",
                    frame=frame,
                    title=f"overview · all {len(setting_groups)} blinded groups",
                    dpi=args.overview_dpi,
                    columns_of_groups=args.blind_columns,
                )
            )
            produced.append(out_path)
        if "blind_unaligned" in kinds:
            for family in families:
                subset = [g for g in setting_groups if g["family"] == family]
                if not subset:
                    continue
                out_path = args.out_dir / f"blind_unaligned_{family}_{setting.lower()}.png"
                figure_log.append(
                    blind_figure(
                        plt,
                        subset,
                        contours,
                        fitter,
                        candidate_codes,
                        query_codes,
                        out_path,
                        setting=setting,
                        view="unaligned",
                        frame=frame,
                        title=(
                            f"family '{family}' · {len(subset)} blinded groups · reference view"
                        ),
                        dpi=args.unaligned_dpi,
                        columns_of_groups=args.blind_columns,
                    )
                )
                produced.append(out_path)

    template_path = args.out_dir / "visual_review_template.csv"
    template_rows = write_template(template_path, list(groups.values()), query_codes)
    produced.append(template_path)

    group_entries: list[dict[str, Any]] = []
    for group in groups.values():
        assignment: dict[str, Any] = {}
        for letter in LETTERS:
            record = group["by_label"].get(letter)
            if record is None:
                continue
            assignment[letter] = {
                "arm": record["arm"],
                "candidate_id": record["candidate_id"],
                "candidate_code": candidate_codes[record["candidate_id"]],
            }
        group_entries.append(
            {
                "setting": group["setting"],
                "query_code": query_codes[group["query_id"]],
                "query_id": group["query_id"],
                "query_family_hidden_from_sheet": group["family"],
                "rank": group["rank"],
                "assignment": assignment,
            }
        )
    mapping = {
        "checkpoint": "D1",
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "git_commit": git_commit(),
        "seed": args.seed,
        "top": args.top,
        "settings": settings,
        "reveal_rule": (
            "this file is the key.  open it only after visual_review_scores.csv is written; "
            "reading it first would make the review a lookup, not a judgement"
        ),
        "inputs": {
            "results.csv": {
                "sha256": sha256_of(results_path),
                "bytes": results_path.stat().st_size,
            },
            "labels.json": {
                "sha256": sha256_of(args.gallery_dir / "labels.json"),
                "bytes": (args.gallery_dir / "labels.json").stat().st_size,
            },
        },
        "rendering": {
            "same_for_every_arm": [
                "one candidate fill and edge colour",
                "one line width",
                f"one square frame per setting {frames}",
                "no smoothing, no resampling, no per-arm style",
            ],
            "hidden_on_the_sheet": [
                "arm name",
                "distance",
                "REL / relevance label",
                "candidate family and role",
                "gallery vs landform provenance",
                "candidate item_id (it encodes family and role)",
            ],
            "kept_on_the_sheet": ["query code", "candidate code", "rank position", "letter"],
        },
        "display_alignment": {
            "module": "earthglyph.retrieval.display_alignment",
            "cost": "symmetric chamfer, rotation only, coarse 3 deg then 0.25 deg refine",
            "changes": "the drawing, never a score or a rank",
            "cache": fitter.save(),
        },
        "query_codes": _reverse(query_codes),
        "candidate_codes": _reverse(candidate_codes),
        "groups": group_entries,
        "cells": len(records),
        "template_rows": template_rows,
    }
    mapping_path = args.out_dir / "blind_mapping.json"
    mapping_path.write_text(
        json.dumps(mapping, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    produced.append(mapping_path)

    report = {
        "settings": settings,
        "families": families,
        "top": args.top,
        "fair": {"settings": fair_settings, "top": args.fair_top, "dpi": args.fair_dpi},
        "dpi": {"blind_pages": args.dpi, "overview": args.overview_dpi,
                "unaligned_pages": args.unaligned_dpi},
        "seed": args.seed,
        "cells": len(records),
        "groups": len(groups),
        "distinct_candidates": len(candidate_codes),
        "frames": frames,
        "library_build_seconds": round(build_seconds, 3),
        "display_pose_seconds_note": "see display_poses.json cache entry count",
        "figures": figure_log,
        "outputs": [p.name for p in produced],
        "total_png_bytes": sum(
            p.stat().st_size for p in produced if p.suffix == ".png"
        ),
    }
    print(json.dumps(report, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
