from __future__ import annotations

import csv
import json
import time
from pathlib import Path

from bg_family_resolver import load_bg_cache


GROUND_TRUTH_PATH = Path(__file__).resolve().with_name("bg_reviewed_ground_truth.json")
REPORT_ROOT = Path("reports/bg_regression_lab")
PAINTABLE = ("m002bg.model", "m004bg.model", "m005bg.model")
PROPOSAL_MARGIN = 0.020
PROPOSAL_WEIGHTS = {
    "uv_outline": 0.60,
    "template_outline": 0.35,
    "uv_precision": 0.05,
    "occupancy": 0.00,
}


def load_reviewed_ground_truth():
    try:
        return json.loads(GROUND_TRUTH_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"schema_version": 1, "exact": {}, "abstain": {}, "exclusions": {}}


def reviewed_pids():
    truth = load_reviewed_ground_truth()
    return set(truth.get("exact") or {}) | set(truth.get("abstain") or {}) | set(truth.get("exclusions") or {})


def _paintable_values(values):
    values = values or {}
    return {name: float(values.get(name, 0.0) or 0.0) for name in PAINTABLE}


def _rank(values):
    ranked = sorted(values.items(), key=lambda item: item[1], reverse=True)
    if not ranked:
        return None, 0.0, []
    margin = ranked[0][1] - ranked[1][1] if len(ranked) > 1 else ranked[0][1]
    return ranked[0][0], float(margin), ranked


def strategy_scores(diagnostic, strategy, occupancy_weight=0.05):
    if strategy == "current_combined":
        return _paintable_values(diagnostic.get("scores"))
    if strategy == "no_occupancy":
        combined = _paintable_values(diagnostic.get("scores"))
        occupancy = _paintable_values(diagnostic.get("occupancy_scores"))
        return {name: combined[name] - occupancy_weight * occupancy[name] for name in PAINTABLE}
    if strategy == "uv_outline":
        return _paintable_values(diagnostic.get("uv_outline_scores"))
    if strategy == "template_outline":
        return _paintable_values(diagnostic.get("template_outline_scores"))
    if strategy == "full_uv_dice":
        return _paintable_values(diagnostic.get("uv_scores"))
    if strategy == "uv_precision":
        return _paintable_values(diagnostic.get("uv_precision_scores"))
    if strategy == "outline_60_35_precision_05":
        uv = _paintable_values(diagnostic.get("uv_outline_scores"))
        template = _paintable_values(diagnostic.get("template_outline_scores"))
        precision = _paintable_values(diagnostic.get("uv_precision_scores"))
        return {
            name: (
                PROPOSAL_WEIGHTS["uv_outline"] * uv[name]
                + PROPOSAL_WEIGHTS["template_outline"] * template[name]
                + PROPOSAL_WEIGHTS["uv_precision"] * precision[name]
            )
            for name in PAINTABLE
        }
    raise ValueError(f"Unknown BG regression strategy: {strategy}")


STRATEGIES = (
    "current_combined",
    "no_occupancy",
    "uv_outline",
    "template_outline",
    "full_uv_dice",
    "uv_precision",
    "outline_60_35_precision_05",
)


def experimental_bg_proposal(diagnostic):
    """Return the dev22 review-only proposal for one cached BG diagnostic.

    This intentionally does not mutate resolver state. A proposal is emitted only
    when the paintable-only 60/35/5 outline score has at least a 0.020 margin and
    the existing sparse-evidence guard is not active.
    """
    if not diagnostic or diagnostic.get("state") != "unresolved":
        return None
    if diagnostic.get("method") != "BG template family ambiguous":
        return None
    if diagnostic.get("sparse_evidence"):
        return None
    scores = strategy_scores(diagnostic, "outline_60_35_precision_05")
    winner, margin, ranked = _rank(scores)
    if not winner or margin < PROPOSAL_MARGIN:
        return None
    return {
        "family": winner,
        "margin": margin,
        "scores": scores,
        "ranked": ranked,
        "strategy": "outline_60_35_precision_05",
    }


def evaluate_bg_regression(cache=None):
    cache = cache or load_bg_cache() or {}
    assignments = cache.get("assignments") or {}
    thresholds = cache.get("thresholds") or {}
    occupancy_weight = float(thresholds.get("occupancy_weight", 0.05) or 0.05)
    truth = load_reviewed_ground_truth()
    exact = truth.get("exact") or {}
    exclusions = truth.get("exclusions") or {}
    abstain = truth.get("abstain") or {}

    summaries = {}
    detail_rows = []
    for strategy in STRATEGIES:
        total = correct = missing = 0
        family_total = {name: 0 for name in PAINTABLE}
        family_correct = {name: 0 for name in PAINTABLE}
        exclusion_tests = exclusion_contradictions = 0
        for pid, expected in exact.items():
            diagnostic = assignments.get(str(pid))
            if not diagnostic:
                missing += 1
                continue
            expected_family = str(expected.get("family") or "")
            scores = strategy_scores(diagnostic, strategy, occupancy_weight)
            predicted, margin, _ = _rank(scores)
            total += 1
            family_total[expected_family] = family_total.get(expected_family, 0) + 1
            is_correct = predicted == expected_family
            correct += int(is_correct)
            family_correct[expected_family] = family_correct.get(expected_family, 0) + int(is_correct)
            detail_rows.append({
                "strategy": strategy,
                "pid": str(pid),
                "expected": expected_family,
                "predicted": predicted or "",
                "correct": is_correct,
                "margin": round(margin, 6),
                "source": expected.get("source", ""),
            })
        for pid, expected in exclusions.items():
            diagnostic = assignments.get(str(pid))
            if not diagnostic:
                continue
            scores = strategy_scores(diagnostic, strategy, occupancy_weight)
            predicted, margin, _ = _rank(scores)
            forbidden = set(expected.get("not_families") or [])
            exclusion_tests += 1
            contradiction = predicted in forbidden
            exclusion_contradictions += int(contradiction)
            detail_rows.append({
                "strategy": strategy,
                "pid": str(pid),
                "expected": "NOT " + "|".join(sorted(forbidden)),
                "predicted": predicted or "",
                "correct": not contradiction,
                "margin": round(margin, 6),
                "source": expected.get("reason", ""),
            })
        summaries[strategy] = {
            "exact_total": total,
            "exact_correct": correct,
            "exact_accuracy": (correct / total) if total else 0.0,
            "missing_exact": missing,
            "by_family": {
                name: {
                    "total": family_total.get(name, 0),
                    "correct": family_correct.get(name, 0),
                    "accuracy": (
                        family_correct.get(name, 0) / family_total.get(name, 0)
                        if family_total.get(name, 0) else 0.0
                    ),
                }
                for name in PAINTABLE
            },
            "exclusion_tests": exclusion_tests,
            "exclusion_contradictions": exclusion_contradictions,
        }

    proposal_reviewed = proposal_correct = 0
    proposal_family = {name: 0 for name in PAINTABLE}
    proposal_family_correct = {name: 0 for name in PAINTABLE}
    for pid, expected in exact.items():
        diagnostic = assignments.get(str(pid))
        proposal = experimental_bg_proposal(diagnostic)
        if not proposal:
            continue
        family = proposal["family"]
        expected_family = str(expected.get("family") or "")
        proposal_reviewed += 1
        proposal_family[expected_family] = proposal_family.get(expected_family, 0) + 1
        ok = family == expected_family
        proposal_correct += int(ok)
        proposal_family_correct[expected_family] = proposal_family_correct.get(expected_family, 0) + int(ok)

    known = reviewed_pids()
    proposal_population = []
    for pid, diagnostic in assignments.items():
        proposal = experimental_bg_proposal(diagnostic)
        if not proposal:
            continue
        proposal_population.append({
            "pid": str(pid),
            "family": proposal["family"],
            "margin": proposal["margin"],
            "reviewed": str(pid) in known,
            "current_family": diagnostic.get("family_model") or "",
        })

    fresh = [row for row in proposal_population if not row["reviewed"]]
    fresh_by_family = {name: sum(1 for row in fresh if row["family"] == name) for name in PAINTABLE}
    changed_fresh = sum(1 for row in fresh if row["family"] != row["current_family"])

    return {
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "cache_version": cache.get("version"),
        "algorithm": cache.get("algorithm"),
        "reviewed_exact_count": len(exact),
        "reviewed_abstain_count": len(abstain),
        "reviewed_exclusion_count": len(exclusions),
        "strategies": summaries,
        "proposal": {
            "name": "outline_60_35_precision_05",
            "weights": PROPOSAL_WEIGHTS,
            "minimum_margin": PROPOSAL_MARGIN,
            "reviewed_accepted": proposal_reviewed,
            "reviewed_correct": proposal_correct,
            "reviewed_accuracy": (proposal_correct / proposal_reviewed) if proposal_reviewed else 0.0,
            "reviewed_by_family": {
                name: {
                    "accepted": proposal_family.get(name, 0),
                    "correct": proposal_family_correct.get(name, 0),
                }
                for name in PAINTABLE
            },
            "all_candidates": len(proposal_population),
            "fresh_candidates": len(fresh),
            "fresh_changed_from_current_winner": changed_fresh,
            "fresh_by_family": fresh_by_family,
            "warning": "Reviewed accuracy is in-sample regression evidence, not independent validation.",
        },
        "details": detail_rows,
    }


def write_bg_regression_report(report):
    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    json_path = REPORT_ROOT / f"{stamp}-bg-regression-lab.json"
    csv_path = REPORT_ROOT / f"{stamp}-bg-regression-lab.csv"
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    fields = ("strategy", "pid", "expected", "predicted", "correct", "margin", "source")
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(report.get("details") or [])
    return json_path, csv_path


def run_bg_regression_lab():
    report = evaluate_bg_regression()
    paths = write_bg_regression_report(report)
    return report, paths
