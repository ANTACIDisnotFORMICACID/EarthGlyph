#!/usr/bin/env python
"""CLI: aggregate P0-6 blind-judgement ratings into the frozen Go/No-Go verdict
(protocol §5-§6, experiments/p0_6_human_queries/PROTOCOL.md).

Reads ONLY collected evidence — session manifests, the judgement manifest, the
gitignored option key and the filled sheet CSVs. It never imports retrieval
code and never generates a rating itself (D91 precedent: aggregation tools
read evidence, they do not produce it). No human data exists yet; every test
run of this script uses synthetic sessions and must pass ``--synthetic``.

Raters
------
``sheet_self_PXX.csv``  drawer self-rating (protocol §5): participant PXX
                        rates the Top-10 options of their own final queries
``sheet_judge_NN.csv``  independent blind judges, every sheeted query

Main criterion (frozen, §6)
---------------------------
"抽屉自评 Top-10 中存在 similar 的有效最终查询占比严格大于 50%" with the
denominator "以登记数为准" (the registered final-query count).

§6's denominator wording admits more than one reading where extraction
refusals and missing self-ratings are concerned, so the report computes ALL
calibers and the verdict uses the strictest — a favourable denominator can
never be selected silently:

``registered_finals``  every registered final query; extraction refusals and
                       queries without a drawer self-rating count as
                       non-success (strictest; matches "以登记数为准")
``sheeted``            queries that passed extraction (refusals excluded);
                       missing self-ratings still count as non-success
``complete_data``      sheeted queries whose self sheet is fully rated

The ratios grow monotonically registered_finals <= sheeted <= complete_data,
so GO under the strictest caliber implies GO under every caliber.

Verdict: ``GO`` requires the frozen collection gate (>=10 participants,
>=6 finals each) AND strict majority (>50%) in ``registered_finals``;
``NO-GO`` otherwise. Hard data errors give ``INVALID`` and exit code 1
(together with NO-GO / NOT READY — exit code is 0 only for GO).

Secondary output (§6): per-judge similar-ratio, judge-vs-self agreement over
commonly rated (query, option) pairs, refusal rate, per-participant
distribution, and optional target attribution when ``--targets`` registers
intended target candidates.

Privacy: the report contains only anonymized ids (PXX, query_id, rater labels)
and aggregate counts — never pseudonyms, filenames, or the A-J -> candidate
mapping (the option key is referenced by sha256 only).

Example
-------
python scripts/score_p06_judgements.py \
    --sessions-root experiments/p0_6_human_queries/sessions \
    --judgement-dir experiments/p0_6_human_queries/judgement
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SHEET_COLUMNS = ["judge_id", "query_id", "option", "verdict", "most_similar",
                 "notes"]
VERDICTS = {"similar", "weak", "not_similar"}
MIN_PARTICIPANTS = 10
MIN_FINALS = 6


@dataclass
class Rating:
    rater: str
    query_id: str
    option: str
    verdict: str
    most_similar: str


@dataclass
class Report:
    synthetic: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    gate: dict[str, Any] = field(default_factory=dict)
    main_criterion: dict[str, Any] = field(default_factory=dict)
    refusal: dict[str, Any] = field(default_factory=dict)
    participants: list[dict[str, Any]] = field(default_factory=list)
    judges: list[dict[str, Any]] = field(default_factory=list)
    agreement: dict[str, Any] = field(default_factory=dict)
    data_quality: dict[str, Any] = field(default_factory=dict)
    attribution: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)
    verdict: str = "INVALID"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_sessions(root: Path) -> tuple[dict[str, list[str]], dict[str, int],
                                       dict[str, int]]:
    """Return per-participant final query ids, practice and invalid counts."""
    finals: dict[str, list[str]] = {}
    practice: dict[str, int] = {}
    invalid: dict[str, int] = {}
    for session_dir in sorted(root.glob("P*")):
        manifest_path = session_dir / "session.json"
        if not manifest_path.is_file():
            continue
        session = json.loads(manifest_path.read_text(encoding="utf-8"))
        participant = str(session["participant"])
        finals[participant] = []
        practice[participant] = 0
        invalid[participant] = 0
        for record in session["files"]:
            if record.get("status") == "invalid":
                invalid[participant] += 1
            elif record.get("final"):
                finals[participant].append(str(record["query_id"]))
            else:
                practice[participant] += 1
    return finals, practice, invalid


def load_sheets(judgement_dir: Path, errors: list[str]) -> list[Rating]:
    ratings: list[Rating] = []
    for path in sorted(judgement_dir.glob("sheet_*.csv")):
        rows = list(csv.reader(path.open(encoding="utf-8")))
        if not rows or rows[0] != SHEET_COLUMNS:
            errors.append(f"{path.name}: header must be {SHEET_COLUMNS}")
            continue
        body = [row for row in rows[1:] if row]
        raters = {row[0] for row in body}
        if len(raters) != 1:
            errors.append(
                f"{path.name}: rows carry {len(raters)} rater ids; a sheet "
                "belongs to exactly one rater"
            )
            continue
        rater = raters.pop()
        if path.name != f"sheet_{rater}.csv":
            errors.append(f"{path.name}: filename does not match rater {rater}")
            continue
        for row in body:
            if len(row) != len(SHEET_COLUMNS):
                errors.append(f"{path.name}: a row has {len(row)} columns")
                continue
            ratings.append(Rating(rater, row[1], row[2], row[3], row[4]))
    return ratings


def validate_and_index(
    ratings: list[Rating],
    sheeted: dict[str, dict[str, dict[str, Any]]],
    refused: set[str],
    errors: list[str],
    warnings: list[str],
) -> tuple[dict[tuple[str, str, str], Rating],
          dict[str, set[tuple[str, str]]]]:
    """Validate every rating row; return a (rater, query, option) index and
    per-rater rated-pair sets."""
    index: dict[tuple[str, str, str], Rating] = {}
    rated: dict[str, set[tuple[str, str]]] = {}
    for rating in ratings:
        key = (rating.rater, rating.query_id, rating.option)
        if key in index:
            errors.append(
                f"duplicate rating: {rating.rater} rated {rating.query_id} "
                f"option {rating.option} more than once"
            )
            continue
        options = sheeted.get(rating.query_id)
        if options is None:
            why = ("extraction-refused, no blind sheet exists"
                   if rating.query_id in refused else
                   "not a sheeted final query (practice, unregistered, or "
                   "registered after sheet generation)")
            errors.append(
                f"{rating.rater}: rating for unknown query_id "
                f"{rating.query_id} ({why})"
            )
            continue
        if rating.option not in options:
            errors.append(
                f"{rating.rater}/{rating.query_id}: illegal option "
                f"{rating.option!r} (sheet offers {sorted(options)})"
            )
            continue
        if rating.verdict not in VERDICTS | {""}:
            errors.append(
                f"{rating.rater}/{rating.query_id}/{rating.option}: illegal "
                f"verdict {rating.verdict!r} (allowed: {sorted(VERDICTS)})"
            )
            continue
        allowed_pick = {""} | {"none"} | set(options)
        if rating.most_similar not in allowed_pick:
            errors.append(
                f"{rating.rater}/{rating.query_id}: illegal most_similar "
                f"{rating.most_similar!r} (allowed: {sorted(options)} or none)"
            )
            continue
        if rating.rater.startswith("self_"):
            drawer = rating.rater[len("self_"):]
            if not rating.query_id.startswith(f"{drawer}-"):
                errors.append(
                    f"identity misalignment: {rating.rater} rated "
                    f"{rating.query_id} — drawers rate only their own queries"
                )
                continue
        index[key] = rating
        rated.setdefault(rating.rater, set()).add(
            (rating.query_id, rating.option))
    # post-pass: a "most similar" pick whose own verdict contradicts it is a
    # data-quality warning, not a hard error (the pick is not part of the
    # frozen criterion)
    for rating in index.values():
        if not rating.most_similar or rating.most_similar == "none":
            continue
        picked = index.get(
            (rating.rater, rating.query_id, rating.most_similar))
        if picked is not None and picked.verdict not in {"similar", ""}:
            warnings.append(
                f"{rating.rater}/{rating.query_id}: most_similar="
                f"{rating.most_similar} but its verdict is "
                f"{picked.verdict!r}"
            )
    return index, rated


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sessions-root", type=Path,
                        default=Path("experiments/p0_6_human_queries/sessions"))
    parser.add_argument("--judgement-dir", type=Path,
                        default=Path("experiments/p0_6_human_queries/judgement"))
    parser.add_argument("--min-participants", type=int,
                        default=MIN_PARTICIPANTS,
                        help="rehearsal runs may lower the frozen gate "
                             "thresholds; every other check stays strict")
    parser.add_argument("--min-finals", type=int, default=MIN_FINALS)
    parser.add_argument("--targets", type=Path, default=None,
                        help="optional CSV (query_id,candidate_id) registering "
                             "each query's intended target for §6 attribution")
    parser.add_argument("--synthetic", action="store_true",
                        help="mark the run and its outputs as SYNTHETIC TEST "
                             "evidence — never human data")
    parser.add_argument("--report", type=Path, default=None)
    args = parser.parse_args(argv)

    report = Report(synthetic=args.synthetic)
    errors, warnings = report.errors, report.warnings

    judgement_manifest_path = args.judgement_dir / "manifest.json"
    key_path = args.sessions_root / "key_options.json"
    for path, what in [(args.sessions_root, "sessions root"),
                       (judgement_manifest_path, "judgement manifest"),
                       (key_path, "option key")]:
        if not path.is_dir() and not path.is_file():
            errors.append(f"{what} missing: {path}")
    if errors:
        return finish(report, args)

    finals, practice, invalid = load_sessions(args.sessions_root)
    judgement_manifest = json.loads(
        judgement_manifest_path.read_text(encoding="utf-8"))
    key_options: dict[str, dict[str, dict[str, Any]]] = json.loads(
        key_path.read_text(encoding="utf-8"))

    recorded = judgement_manifest.get("key_options_sha256")
    if recorded is not None and recorded != sha256_file(key_path):
        errors.append(
            "key_options.json sha256 differs from the judgement manifest — "
            "the blind-review key changed after sheet generation"
        )

    queries: dict[str, Any] = judgement_manifest.get("queries", {})
    sheeted = {q: options for q, options in key_options.items()
               if "refusal" not in queries.get(q, {"refusal": True})}
    for query_id in queries:
        if "refusal" not in queries[query_id] and query_id not in key_options:
            errors.append(
                f"{query_id}: sheet generated but absent from the option key")
        if query_id not in queries and query_id in key_options:
            errors.append(
                f"{query_id}: option key entry without a judgement-manifest "
                "record")
    for query_id, options in sheeted.items():
        identities: dict[tuple[Any, Any], list[str]] = {}
        for option, entry in options.items():
            identity = (entry.get("row"), entry.get("candidate_id"))
            identities.setdefault(identity, []).append(option)
        for identity, option_list in identities.items():
            if len(option_list) > 1:
                errors.append(
                    f"{query_id}: options {sorted(option_list)} map to the "
                    f"same candidate (row={identity[0]}, "
                    f"candidate_id={identity[1]}) — verdicts would not be "
                    "uniquely traceable"
                )

    refusals = sorted(q for q in queries if "refusal" in queries[q])
    refused = set(refusals)
    registered = {q: participant for participant, qs in finals.items()
                  for q in qs}
    if judgement_manifest.get("n_final_queries") != len(registered):
        errors.append(
            f"dataset changed after sheet generation: manifest recorded "
            f"{judgement_manifest.get('n_final_queries')} final queries, "
            f"sessions now register {len(registered)} — regenerate sheets")
    if (set(sheeted) | refused) != set(registered):
        errors.append(
            "sheeted + refused queries do not equal the registered final "
            "queries — regenerate sheets before scoring")

    ratings = load_sheets(args.judgement_dir, errors)
    index, rated = validate_and_index(ratings, sheeted, refused, errors,
                                      warnings)
    if errors:
        return finish(report, args)

    refusal_rate = (len(refusals) / len(registered)) if registered else 0.0

    # drawer self-ratings per query (any rated option and similar verdicts)
    self_rated: dict[str, set[str]] = {}
    self_similar: dict[str, bool] = {}
    for (rater, query_id, _), rating in index.items():
        if rater.startswith("self_"):
            self_rated.setdefault(query_id, set()).add(rating.option)
            self_similar[query_id] = self_similar.get(query_id, False) or (
                rating.verdict == "similar")
    fully_rated = {q for q, options in sheeted.items()
                   if self_rated.get(q) == set(options)}

    def ratio(num: int, den: int) -> float | None:
        return num / den if den else None

    similar_count = sum(1 for q in registered if self_similar.get(q))
    sheeted_similar = sum(1 for q in sheeted if self_similar.get(q))
    complete_similar = sum(1 for q in fully_rated if self_similar.get(q))
    calibers = {
        "registered_finals": {
            "denominator": len(registered), "similar": similar_count,
            "ratio": ratio(similar_count, len(registered))},
        "sheeted": {
            "denominator": len(sheeted), "similar": sheeted_similar,
            "ratio": ratio(sheeted_similar, len(sheeted))},
        "complete_data": {
            "denominator": len(fully_rated), "similar": complete_similar,
            "ratio": ratio(complete_similar, len(fully_rated))},
    }

    # internal cross-check: recount the strict numerator row-driven
    recount = {r.query_id for r in ratings
               if r.rater.startswith("self_") and r.verdict == "similar"
               and (r.rater, r.query_id, r.option) in index}
    if recount != {q for q in registered if self_similar.get(q)}:
        errors.append("internal cross-check failed: row-driven recount of the "
                      "main criterion disagrees with the primary computation")
        return finish(report, args)
    per_participant_sum = sum(
        1 for participant in finals for q in finals[participant]
        if self_similar.get(q))
    if per_participant_sum != similar_count:
        errors.append("internal cross-check failed: per-participant sums "
                      "disagree with the global similar count")
        return finish(report, args)

    gate_ok = (len(finals) >= args.min_participants
               and all(len(qs) >= args.min_finals for qs in finals.values()))
    short = [f"{p}({len(qs)})" for p, qs in sorted(finals.items())
             if len(qs) < args.min_finals]
    report.gate = {
        "min_participants": args.min_participants,
        "min_finals": args.min_finals,
        "participants": len(finals),
        "short_participants": short,
        "passed": gate_ok,
    }

    strict = calibers["registered_finals"]["ratio"]
    if not gate_ok:
        verdict = "NOT READY"
    elif strict is not None and strict > 0.5:
        verdict = "GO"
    else:
        verdict = "NO-GO"

    report.main_criterion = {
        "criterion": "drawer self-rating Top-10 contains >=1 similar, "
                     "ratio over registered final queries, GO = strictly >50%",
        "calibers": calibers,
        "verdict_caliber": "registered_finals (strictest; monotone in the "
                           "other calibers)",
    }
    report.refusal = {
        "refused_queries": refusals,
        "refusal_rate": refusal_rate,
        "note": "refusals count as non-success in registered_finals and are "
                "excluded from sheeted; the two calibers diverge exactly by "
                "the refusal rate's impact",
    }

    for participant in sorted(finals):
        own = finals[participant]
        report.participants.append({
            "participant": participant,
            "finals": len(own),
            "practice": practice[participant],
            "invalid": invalid[participant],
            "sheeted": sum(1 for q in own if q in sheeted),
            "refused": sum(1 for q in own if q in refusals),
            "self_similar": sum(1 for q in own if self_similar.get(q)),
            "ratio_registered": ratio(sum(1 for q in own if self_similar.get(q)),
                                      len(own)),
        })

    judge_rows: list[dict[str, Any]] = []
    agreement_rows: dict[str, dict[str, Any]] = {}
    for rater in sorted(rated):
        if rater.startswith("self_"):
            continue
        pairs_rated = rated[rater]
        queries_rated = {q for q, _ in pairs_rated}
        judge_similar = sum(
            1 for q in queries_rated
            if any(index[(rater, q, o)].verdict == "similar" for o in sheeted[q]
                   if (rater, q, o) in index))
        common = 0
        matches = 0
        for query_id, option in pairs_rated:
            self_key = (f"self_{query_id[:3]}", query_id, option)
            if self_key in index and index[self_key].verdict != "":
                common += 1
                if index[self_key].verdict == index[(rater, query_id, option)].verdict:
                    matches += 1
        judge_rows.append({
            "rater": rater,
            "queries_rated": len(queries_rated),
            "sheeted_queries": len(sheeted),
            "coverage": ratio(len(queries_rated), len(sheeted)),
            "similar_queries": judge_similar,
            "similar_ratio": ratio(judge_similar, len(queries_rated)),
        })
        agreement_rows[rater] = {
            "common_pairs": common, "matching_pairs": matches,
            "agreement": ratio(matches, common),
        }
    report.judges = judge_rows
    report.agreement = {
        "definition": "exact verdict match between a judge and the drawer "
                      "over (query, option) pairs both rated",
        "per_judge": agreement_rows,
    }

    self_coverage = {}
    for participant in sorted(finals):
        own_sheeted = [q for q in finals[participant] if q in sheeted]
        complete = sum(1 for q in own_sheeted
                       if self_rated.get(q) == set(sheeted[q]))
        self_coverage[participant] = {
            "sheeted": len(own_sheeted), "fully_rated": complete}
    report.data_quality = {
        "self_coverage": self_coverage,
        "warnings": warnings,
    }

    if args.targets is not None:
        attribution = {"target_top1": 0, "target_in_top10": 0,
                       "target_not_in_top10": 0, "unknown": 0}
        with args.targets.open(encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                query_id, candidate = row["query_id"], row["candidate_id"]
                target_options = sheeted.get(query_id)
                if target_options is None:
                    attribution["unknown"] += 1
                    continue
                candidate_to_option = {str(entry["candidate_id"]): option
                                       for option, entry in target_options.items()}
                if candidate in candidate_to_option:
                    top1_row = queries[query_id].get("top1_row")
                    target_row = target_options[
                        candidate_to_option[candidate]]["row"]
                    attribution[
                        "target_top1" if target_row == top1_row
                        else "target_in_top10"] += 1
                else:
                    attribution["target_not_in_top10"] += 1
        report.attribution = {"status": "computed", "counts": attribution}
    else:
        report.attribution = {
            "status": "not_computed",
            "reason": "no --targets registration; protocol §6 secondary "
                      "attribution needs the intended target per query "
                      "(session manifests record no topic field — see "
                      "SCORING_GUIDE.md)",
        }

    report.provenance = {
        "judgement_manifest_sha256": sha256_file(judgement_manifest_path),
        "key_options_sha256": sha256_file(key_path),
        "sheet_sha256": {
            path.name: sha256_file(path)
            for path in sorted(args.judgement_dir.glob("sheet_*.csv"))},
        "n_registered_finals": len(registered),
        "n_sheeted": len(sheeted),
        "n_refused": len(refusals),
    }
    report.verdict = verdict
    return finish(report, args)


def finish(report: Report, args: argparse.Namespace) -> int:
    report_path = args.report or (args.judgement_dir / "scoring_report.json")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report.__dict__, indent=1, ensure_ascii=False),
        encoding="utf-8")

    if report.synthetic:
        print("SYNTHETIC TEST — synthetic inputs; not human evidence")
    for error in report.errors:
        print(f"ERROR: {error}", file=sys.stderr)
    if report.main_criterion:
        for name, rows in report.main_criterion["calibers"].items():
            ratio_txt = ("n/a" if rows["ratio"] is None
                         else f"{rows['ratio']:.4f}")
            print(f"  caliber {name}: {rows['similar']}/{rows['denominator']}"
                  f" = {ratio_txt}")
        print(f"  refusal rate: {report.refusal['refusal_rate']:.4f}")
        for judge in report.judges:
            row = report.agreement["per_judge"].get(judge["rater"], {})
            agree = row.get("agreement")
            agree_txt = "n/a" if agree is None else f"{agree:.4f}"
            print(f"  judge {judge['rater']}: similar_ratio "
                  f"{judge['similar_ratio']}, self-agreement {agree_txt}")
    print(f"VERDICT: {report.verdict}"
          f"{' (INVALID — hard data errors)' if report.errors else ''}")
    print(f"report written to {report_path}")
    return 0 if (report.verdict == "GO" and not report.errors) else 1


if __name__ == "__main__":
    raise SystemExit(main())
