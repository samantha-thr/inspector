from __future__ import annotations

import csv
import json
import re
import time
from collections import defaultdict
from pathlib import Path

from bg_family_resolver import load_bg_cache


REPORT_ROOT = Path(__file__).resolve().parent / "reports" / "bg_template_reviews"
GROUND_TRUTH_PATH = Path(__file__).resolve().with_name("bg_reviewed_ground_truth.json")

VOTE_TO_MODEL = {
    "2 Seater": "m002bg.model",
    "TUV": "m004bg.model",
    "4 Seater": "m005bg.model",
    "Unknown": "",
}


def _pid_slot(path):
    match = re.match(r"^(\d+)_([1-9]\d*)\.", Path(str(path)).name, re.IGNORECASE)
    return (match.group(1), int(match.group(2))) if match else (None, None)


def _body_paths(sets):
    result = {}
    for pid, paths in sets:
        body = None
        for path in paths:
            parsed_pid, slot = _pid_slot(path)
            if parsed_pid == str(pid) and slot == 1:
                body = str(path)
                break
        if body:
            result[str(pid)] = body
    return result


def _known_reviewed_pids():
    known = set()
    try:
        data = json.loads(GROUND_TRUTH_PATH.read_text(encoding="utf-8"))
        for key in ("exact", "abstain", "exclusions"):
            known.update(str(pid) for pid in (data.get(key) or {}))
    except Exception:
        pass
    if REPORT_ROOT.exists():
        for path in REPORT_ROOT.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                for row in data.get("reviews") or []:
                    if row.get("vote"):
                        known.add(str(row.get("pid")))
            except Exception:
                continue
    return known


def _review_row(pid, body, diagnostic):
    return {
        "pid": str(pid),
        "body_texture": str(body),
        "family_model": str(diagnostic.get("family_model") or ""),
        "uv_winner": str(diagnostic.get("uv_winner") or ""),
        "template_winner": str(diagnostic.get("template_winner") or ""),
        "evidence_confidence": float(diagnostic.get("evidence_confidence") or 0.0),
        "evidence_band": str(diagnostic.get("evidence_band") or ""),
        "score": float(diagnostic.get("score") or 0.0),
        "margin": float(diagnostic.get("margin") or 0.0),
        "votes": int(diagnostic.get("votes") or 0),
        "primary_agree": bool(diagnostic.get("primary_agree")),
        "sparse_evidence": bool(diagnostic.get("sparse_evidence")),
        "regression_family": str(diagnostic.get("regression_family") or ""),
        "regression_score": float(diagnostic.get("regression_score") or 0.0),
        "regression_margin": float(diagnostic.get("regression_margin") or 0.0),
        "regression_template_support": float(diagnostic.get("regression_template_support") or 0.0),
        "decision_reason": str(diagnostic.get("decision_reason") or ""),
        "scores_json": json.dumps(diagnostic.get("scores") or {}, sort_keys=True),
        "uv_outline_scores_json": json.dumps(diagnostic.get("uv_outline_scores") or {}, sort_keys=True),
        "template_outline_scores_json": json.dumps(diagnostic.get("template_outline_scores") or {}, sort_keys=True),
        "uv_precision_scores_json": json.dumps(diagnostic.get("uv_precision_scores") or {}, sort_keys=True),
        "occupancy_scores_json": json.dumps(diagnostic.get("occupancy_scores") or {}, sort_keys=True),
        "occupancy_json": json.dumps(diagnostic.get("occupancy") or {}, sort_keys=True),
    }


def unresolved_template_population(sets, skip_reviewed=True):
    cache = load_bg_cache() or {}
    assignments = cache.get("assignments") or {}
    bodies = _body_paths(sets)
    known = _known_reviewed_pids() if skip_reviewed else set()
    rows = []
    for pid, diagnostic in assignments.items():
        if diagnostic.get("state") != "unresolved":
            continue
        if diagnostic.get("method") != "BG template family ambiguous":
            continue
        if skip_reviewed and str(pid) in known:
            continue
        body = bodies.get(str(pid))
        if not body:
            continue
        rows.append(_review_row(pid, body, diagnostic))
    return rows


def reviewed_m002_population(sets, skip_reviewed=True):
    cache=load_bg_cache() or {}
    assignments=cache.get("assignments") or {}
    bodies=_body_paths(sets)
    known=_known_reviewed_pids() if skip_reviewed else set()
    rows=[]
    for pid,d in assignments.items():
        if d.get("state")=="resolved" and d.get("confidence_tier")=="reviewed-m002-template":
            if skip_reviewed and str(pid) in known:
                continue
            body=bodies.get(str(pid))
            if body:
                rows.append(_review_row(pid,body,d))
    return rows


def _diverse(rows):
    buckets = defaultdict(list)
    for row in rows:
        key = (
            row["family_model"],
            row["uv_winner"],
            row["template_winner"],
            row["evidence_band"],
            row["sparse_evidence"],
        )
        buckets[key].append(row)
    for bucket in buckets.values():
        bucket.sort(key=lambda r: (r["evidence_confidence"], r["regression_margin"], int(r["pid"])))
    ordered_buckets = sorted(
        buckets.values(),
        key=lambda bucket: (
            len(bucket),
            bucket[0]["family_model"] if bucket else "",
            bucket[0]["uv_winner"] if bucket else "",
            bucket[0]["template_winner"] if bucket else "",
        ),
        reverse=True,
    )
    result = []
    index = 0
    while True:
        added = False
        for bucket in ordered_buckets:
            if index < len(bucket):
                result.append(bucket[index])
                added = True
        if not added:
            break
        index += 1
    return result


def select_template_review_batch(sets, count=25, mode="Diverse unresolved", skip_reviewed=True):
    rows = unresolved_template_population(sets, skip_reviewed=skip_reviewed)

    # dev26 blind review produced several clean hypotheses. These targeted modes
    # are validation populations only: they do not change production assignments.
    if mode == "Validate m002 + template support >= .200":
        rows = [
            r for r in rows
            if r["family_model"] == "m002bg.model" and float(json.loads(r["template_outline_scores_json"]).get("m002bg.model", 0.0) or 0.0) >= 0.200
        ]
    elif mode == "Validate m515 + regression m004 + margin >= .020":
        rows = [
            r for r in rows
            if r["family_model"] == "m515bf.model"
            and r["regression_family"] == "m004bg.model"
            and r["margin"] >= 0.020
        ]
    elif mode == "Validate m001 candidate":
        rows = [r for r in rows if r["family_model"] == "m001bg.model"]
    elif mode == "Validate m002 candidate":
        rows = [r for r in rows if r["family_model"] == "m002bg.model"]
    elif mode == "Validate m005 candidate":
        rows = [r for r in rows if r["family_model"] == "m005bg.model"]

    population = len(rows)
    if mode == "Closest to auto-resolve":
        rows.sort(key=lambda r: (-r["regression_margin"], -r["regression_template_support"], -r["evidence_confidence"], int(r["pid"])))
    elif mode == "Lowest evidence":
        rows.sort(key=lambda r: (r["evidence_confidence"], r["score"], int(r["pid"])))
    elif mode == "Highest evidence":
        rows.sort(key=lambda r: (-r["evidence_confidence"], -r["score"], int(r["pid"])))
    elif mode == "PID order":
        rows.sort(key=lambda r: int(r["pid"]))
    elif mode.startswith("Validate "):
        rows.sort(key=lambda r: (-r["evidence_confidence"], -r["score"], int(r["pid"])))
    else:
        rows = _diverse(rows)
    return rows[: max(1, int(count))], population


REPORT_COLUMNS = [
    "created", "pid", "vote", "vote_model", "body_texture",
    "family_model", "uv_winner", "template_winner",
    "evidence_confidence", "evidence_band", "score", "margin", "votes",
    "primary_agree", "sparse_evidence",
    "regression_family", "regression_score", "regression_margin",
    "regression_template_support", "decision_reason",
    "scores_json", "uv_outline_scores_json", "template_outline_scores_json",
    "uv_precision_scores_json", "occupancy_scores_json", "occupancy_json",
]


def write_template_review_report(rows, mode):
    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    base = REPORT_ROOT / f"{stamp}-bg-template-review"
    created = time.strftime("%Y-%m-%d %H:%M:%S")
    reviews = []
    for source in rows:
        row = dict(source)
        row["created"] = created
        row["vote_model"] = VOTE_TO_MODEL.get(row.get("vote") or "", "")
        reviews.append(row)
    payload = {
        "created": created,
        "scope": "BG unresolved template manual review",
        "selection_mode": mode,
        "review_count": len(reviews),
        "vote_counts": {
            label: sum(1 for row in reviews if row.get("vote") == label)
            for label in VOTE_TO_MODEL
        },
        "reviews": reviews,
    }
    json_path = base.with_suffix(".json")
    csv_path = base.with_suffix(".csv")
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=REPORT_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(reviews)
    return {"json": str(json_path), "csv": str(csv_path), "payload": payload}
