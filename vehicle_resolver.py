from __future__ import annotations
import json,re
from pathlib import Path

RULES_PATH=Path("vehicle_resolution_rules.json")


def resource_identity(filename):
    """Classify There client resources by their leading filename character.

    Numeric-leading resources are product/PID content. Alphabetic-leading
    resources are official/client resources. This records the client naming
    convention only; it does not attempt to identify a human author.
    """
    name=Path(filename).name
    stem=Path(name).stem
    if not stem:
        return {"origin":"unknown","pid":None}
    if stem[0].isdigit():
        m=re.match(r"^(\d+)",stem)
        return {"origin":"product","pid":m.group(1) if m else None}
    if stem[0].isalpha():
        return {"origin":"official","pid":None}
    return {"origin":"unknown","pid":None}


def model_pid(filename):
    """Return PID only for a bare numeric-leading product model.

    Examples:
      95054471.model -> 95054471
      m000hbk_sportbike.model -> None (official resource)
      mhc001hb_lightning.model -> None (official resource)
    """
    ident=resource_identity(filename)
    return ident["pid"] if ident["origin"]=="product" else None


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
        assignments.append({"pid":pid,"textures":paths,"model":model,"method":method,"state":state,"origin":"product"})
    return assignments


def resolution_summary(assignments):
    out={"resolved":0,"unresolved":0,"by_model":{},"by_method":{}}
    for x in assignments:
        out[x["state"]]+=1
        out["by_method"][x["method"]]=out["by_method"].get(x["method"],0)+1
        if x["model"]:
            name=x["model"]["filename"];out["by_model"][name]=out["by_model"].get(name,0)+1
    return out


def _printable_strings(path, limit=40):
    """Extract diagnostic strings from a config without interpreting unknown fields."""
    try:
        data=Path(path).read_bytes()
    except Exception:
        return []
    found=[]
    for m in re.finditer(rb"[\x20-\x7e]{4,}",data):
        text=m.group(0).decode("latin1","replace").strip()
        if text and text not in found:found.append(text)
        if len(found)>=limit:break
    # Some client configs may contain UTF-16LE text. Keep this evidence separate.
    try:
        decoded=data.decode("utf-16le",errors="ignore")
        for text in re.findall(r"[ -~]{4,}",decoded):
            text=text.strip()
            if text and text not in found:found.append(text)
            if len(found)>=limit:break
    except Exception:
        pass
    return found


def aconf_evidence(pid, texture_paths):
    """Return presence/raw-string evidence for the sibling PID .aconf file."""
    if not texture_paths:return {"exists":False,"path":None,"strings":[]}
    parent=Path(texture_paths[0]).parent
    candidates=[parent/f"{pid}.aconf"]
    # Preserve case-insensitive Windows behavior when the exact path isn't present.
    if not candidates[0].exists():
        try:
            candidates.extend(p for p in parent.glob("*.aconf") if p.stem.lower()==str(pid).lower())
        except Exception:pass
    hit=next((p for p in candidates if p.exists()),None)
    return {"exists":bool(hit),"path":str(hit) if hit else None,"strings":_printable_strings(hit) if hit else []}


def folder_configuration(folder, models, textures):
    """Inventory resource naming/provenance and configuration evidence for one folder."""
    model_rows=[dict(m) for m in models]
    texture_rows=[dict(t) for t in textures]
    product_models=[m for m in model_rows if resource_identity(m["filename"])["origin"]=="product"]
    official_models=[m for m in model_rows if resource_identity(m["filename"])["origin"]=="official"]
    product_textures=[t for t in texture_rows if resource_identity(t["filename"])["origin"]=="product"]
    official_textures=[t for t in texture_rows if resource_identity(t["filename"])["origin"]=="official"]
    parents=[]
    for row in model_rows+texture_rows:
        try:
            p=Path(row["path"]).parent
            if p not in parents:parents.append(p)
        except Exception:pass
    aconf=[]
    for parent in parents:
        try:
            for p in parent.glob("*.aconf"):
                ident=resource_identity(p.name)
                aconf.append({"filename":p.name,"path":str(p),"origin":ident["origin"],"pid":ident["pid"]})
        except Exception:pass
    return {
        "folder":folder,
        "product_models":product_models,
        "official_models":official_models,
        "product_textures":product_textures,
        "official_textures":official_textures,
        "aconf":aconf,
        "product_aconf":[x for x in aconf if x["origin"]=="product"],
        "official_aconf":[x for x in aconf if x["origin"]=="official"],
    }


def enrich_assignments(assignments):
    """Attach non-inferential sibling config evidence to resolver output."""
    out=[]
    for x in assignments:
        y=dict(x);y["aconf"]=aconf_evidence(y["pid"],y["textures"]);out.append(y)
    return out
