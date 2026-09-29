from __future__ import annotations
import json,re
from pathlib import Path

RULES_PATH=Path("vehicle_resolution_rules.json")


def model_pid(filename):
    """Return an explicit numeric product id only when the model filename encodes it."""
    stem=Path(filename).stem.lower()
    # Custom/product models may be m<PID><folder>. Never treat short stock ids as product PIDs.
    m=re.match(r"^m(\d{5,})(?:[a-z].*)?$",stem)
    return m.group(1) if m else None


def load_rules():
    if not RULES_PATH.exists():return {}
    try:return json.loads(RULES_PATH.read_text(encoding="utf-8"))
    except Exception:return {}


def resolve_products(folder,models,product_sets):
    """Conservative resolver: exact PID is authoritative; configured rules are explicit.

    No image similarity or inferred model family is used here.
    """
    folder=folder.lower();rules=load_rules().get(folder,{})
    exact={}
    for m in models:
        pid=model_pid(m["filename"])
        if pid:exact[pid]=dict(m)
    by_name={m["filename"].lower():dict(m) for m in models}
    base_name=str(rules.get("base_model","")).lower()
    base=by_name.get(base_name)
    nonpaintable={str(x).lower() for x in rules.get("nonpaintable_models",[])}
    assignments=[]
    for pid,paths in product_sets:
        if pid in exact:
            model=exact[pid];method="exact PID model";state="resolved"
        elif base and base["filename"].lower() not in nonpaintable:
            model=base;method="configured base model";state="resolved"
        else:
            model=None;method="no authoritative model rule";state="unresolved"
        assignments.append({"pid":pid,"textures":paths,"model":model,"method":method,"state":state})
    return assignments


def resolution_summary(assignments):
    out={"resolved":0,"unresolved":0,"by_model":{},"by_method":{}}
    for x in assignments:
        out[x["state"]]+=1
        out["by_method"][x["method"]]=out["by_method"].get(x["method"],0)+1
        if x["model"]:
            name=x["model"]["filename"];out["by_model"][name]=out["by_model"].get(name,0)+1
    return out
