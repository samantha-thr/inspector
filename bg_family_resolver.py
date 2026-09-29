from __future__ import annotations

import csv
import hashlib
import json
import math
import re
import time
from pathlib import Path

from PIL import Image, ImageChops, ImageFilter, ImageStat, ImageDraw

from there_texture_decoder import open_texture_image
from there_model_decoder import decode_model


CACHE_VERSION = 3
CACHE_PATH = Path("cache/bg_family_resolution.json")
REPORT_ROOT = Path("reports/bg_family_analysis")
QUANTILES = (0.70, 0.80, 0.85)
MIN_WINNER_SCORE = 0.18
MIN_MARGIN = 0.035
BACKGROUND_TOLERANCE = 38
OCCUPANCY_WEIGHT = 0.25
MODEL_UV_WEIGHT = 0.75
MIN_BORDER_BACKGROUND_SHARE = 0.15
MIN_FOREGROUND_FRACTION = 0.04
MAX_FOREGROUND_FRACTION = 0.96
MIN_TEXTURE_STDDEV = 5.0


def _slot(path):
    m=re.match(r"^\d+_([1-9]\d*)\.",Path(path).name,re.IGNORECASE)
    return int(m.group(1)) if m else None


def _find_reference(parent: Path, name: str):
    wanted=str(name or "").lower()
    candidates=[parent/name,parent/(name+".dds")]
    for p in candidates:
        if p.exists():return p
    try:
        for p in parent.iterdir():
            low=p.name.lower()
            if low==wanted or low==wanted+".dds":
                return p
    except OSError:
        pass
    return None


def _percentile_threshold(image: Image.Image, quantile: float):
    hist=image.histogram()
    total=sum(hist)
    target=max(1,int(total*quantile))
    running=0
    for i,count in enumerate(hist):
        running+=count
        if running>=target:return i
    return 255


def _activity_mask(path, quantile=0.80, size=256, feature_size=64):
    """Artwork-agnostic-ish spatial activity mask.

    It intentionally ignores color identity and focuses on where structural
    changes occur on the flat template. This is materially more selective than
    the old 8x8 mean-edge fingerprint.
    """
    im=open_texture_image(path).convert("RGB").resize((size,size),Image.Resampling.LANCZOS)
    r,g,b=im.split()
    er=r.filter(ImageFilter.FIND_EDGES)
    eg=g.filter(ImageFilter.FIND_EDGES)
    eb=b.filter(ImageFilter.FIND_EDGES)
    edge=ImageChops.lighter(ImageChops.lighter(er,eg),eb).filter(ImageFilter.GaussianBlur(1.25))
    threshold=_percentile_threshold(edge,quantile)
    binary=edge.point(lambda p:255 if p>=threshold else 0,"L").filter(ImageFilter.MaxFilter(5))
    small=binary.resize((feature_size,feature_size),Image.Resampling.BOX)
    return bytes(1 if p>=96 else 0 for p in small.getdata())


def _mask_iou(a: bytes,b: bytes):
    inter=union=0
    for x,y in zip(a,b):
        if x or y:union+=1
        if x and y:inter+=1
    return inter/union if union else 0.0


def _foreground_occupancy(path, size=256, feature_size=64):
    """Estimate UV-island occupancy while ignoring the texture's background color.

    The background color is learned independently from the dominant quantized
    color along that image's border. This means a designer may change the
    background from the stock gray to black/green/etc. without defeating the
    comparison. All pixels close to that learned background are excluded.
    """
    im=open_texture_image(path).convert("RGB").resize((size,size),Image.Resampling.LANCZOS)
    stat=ImageStat.Stat(im)
    stddev=sum(stat.stddev)/3.0
    pixels=im.load()

    border=[]
    for x in range(size):
        border.append(pixels[x,0]);border.append(pixels[x,size-1])
    for y in range(size):
        border.append(pixels[0,y]);border.append(pixels[size-1,y])

    # Quantization makes the estimate tolerant of DDS/JPEG compression noise.
    step=16
    buckets={}
    members={}
    for rgb in border:
        key=tuple((int(v)//step)*step + step//2 for v in rgb)
        buckets[key]=buckets.get(key,0)+1
        members.setdefault(key,[]).append(rgb)
    dominant=max(buckets,key=buckets.get)
    samples=members[dominant]
    bg=tuple(sum(p[i] for p in samples)/len(samples) for i in range(3))
    border_share=buckets[dominant]/max(1,len(border))

    fg=Image.new("L",(size,size),0)
    out=fg.load();foreground=0
    tol2=BACKGROUND_TOLERANCE*BACKGROUND_TOLERANCE
    for y in range(size):
        for x in range(size):
            r,g,b=pixels[x,y]
            d2=(r-bg[0])**2+(g-bg[1])**2+(b-bg[2])**2
            is_fg=d2>tol2
            if is_fg:
                out[x,y]=255;foreground+=1

    foreground_fraction=foreground/float(size*size)
    usable=(border_share>=MIN_BORDER_BACKGROUND_SHARE and
            MIN_FOREGROUND_FRACTION<=foreground_fraction<=MAX_FOREGROUND_FRACTION)
    small=fg.resize((feature_size,feature_size),Image.Resampling.BOX)
    mask=bytes(1 if p>=96 else 0 for p in small.getdata())
    return {
        "mask":mask,
        "background_rgb":[round(v,2) for v in bg],
        "border_background_share":round(border_share,6),
        "foreground_fraction":round(foreground_fraction,6),
        "stddev":round(stddev,6),
        "usable":usable,
        "low_information":stddev<MIN_TEXTURE_STDDEV,
    }


def _texture_key(value):
    name=str(value or "").replace("\\","/").rsplit("/",1)[-1].lower()
    changed=True
    while changed:
        changed=False
        for ext in (".dds",".png",".jpg",".jpeg",".tga",".bmp"):
            if name.endswith(ext):
                name=name[:-len(ext)];changed=True
    return name


def _model_body_uv_mask(model_path, template_name, size=256, feature_size=64):
    """Build a filled UV coverage mask from the model material that owns the stock body map."""
    model=decode_model(model_path)
    wanted=_texture_key(template_name)
    material_ids=[]
    for material in model.materials:
        color_ref=material.texture_maps.get(0)
        if color_ref and _texture_key(color_ref)==wanted:
            material_ids.append(material.index)
    if not material_ids:
        for material in model.materials:
            if any(_texture_key(ref)==wanted for ref in material.texture_maps.values()):
                material_ids.append(material.index)
    if not material_ids:
        raise ValueError(f"{Path(model_path).name}: body material for {template_name} was not found")

    mask=Image.new("L",(size,size),0);draw=ImageDraw.Draw(mask);triangles=0
    if not model.lods:
        raise ValueError(f"{Path(model_path).name}: no LODs")
    for mesh in model.lods[0].meshes:
        if mesh.material_index not in material_ids:continue
        verts=mesh.vertices
        for i in range(0,len(mesh.indices)-2,3):
            ids=mesh.indices[i:i+3]
            if any(idx>=len(verts) or verts[idx].uv0 is None for idx in ids):continue
            pts=[]
            for idx in ids:
                u,v=verts[idx].uv0
                pts.append((round(u*(size-1)),round((1.0-v)*(size-1))))
            draw.polygon(pts,fill=255);triangles+=1
    small=mask.resize((feature_size,feature_size),Image.Resampling.BOX)
    bits=bytes(1 if p>=64 else 0 for p in small.getdata())
    area=sum(bits)
    if triangles==0 or area==0:
        raise ValueError(f"{Path(model_path).name}: body material has no usable UV triangles")
    return {"mask":bits,"triangles":triangles,"area":area,"material_ids":material_ids}


def _mask_metrics(a: bytes,b: bytes):
    inter=union=ac=bc=0
    for x,y in zip(a,b):
        if x:ac+=1
        if y:bc+=1
        if x or y:union+=1
        if x and y:inter+=1
    return {
        "iou":inter/union if union else 0.0,
        "dice":(2.0*inter/(ac+bc)) if (ac+bc) else 0.0,
        "precision":inter/ac if ac else 0.0,
        "coverage":inter/bc if bc else 0.0,
    }


def _file_token(path: Path):
    st=path.stat()
    return f"{path.resolve()}|{st.st_size}|{st.st_mtime_ns}"


def _anchor_fingerprint(anchors):
    payload="\n".join(sorted(f"{x['model']}|{x['paintable']}|{_file_token(x['path'])}" for x in anchors))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _load_rules(rules_path=Path("vehicle_resolution_rules.json")):
    try:return json.loads(Path(rules_path).read_text(encoding="utf-8"))
    except Exception:return {}


def _references(product_sets, models, rules=None):
    rules=rules or _load_rules()
    bg=(rules or {}).get("bg",{})
    verified=bg.get("verified_template_models",{})
    by_model={str(m["filename"]).lower():dict(m) for m in models}
    parent=None
    for _,paths in product_sets:
        if paths:
            parent=Path(paths[0]).parent
            break
    if parent is None:return []
    refs=[]
    for model_name,info in verified.items():
        model=by_model.get(str(model_name).lower())
        body=info.get("body")
        if not model or not body:continue
        path=_find_reference(parent,body)
        if not path:continue
        refs.append({
            "model":model["filename"],
            "model_path":model["path"],
            "template":path.name,
            "path":path,
            "paintable":bool(info.get("paintable",True)),
        })
    return refs


def analyze_bg_families(product_sets,models,rules=None):
    """Classify BG body textures against preview-verified stock template anchors.

    The primary evidence is UV-island occupancy after each image's dominant
    background color is removed. Structural edge similarity remains secondary
    evidence. Products with too little visual information, including genuinely
    solid-color designs, remain unresolved rather than being forced into a
    family.
    """
    anchors=_references(product_sets,models,rules)
    if len(anchors)<2:
        return {"ok":False,"message":"Fewer than two verified BG reference templates were found in the indexed client folder.","assignments":{},"anchors":[]}

    usable_anchors=[]
    for anchor in anchors:
        anchor["masks"]={str(q):_activity_mask(anchor["path"],q) for q in QUANTILES}
        anchor["occupancy"]=_foreground_occupancy(anchor["path"])
        try:
            anchor["model_uv"]=_model_body_uv_mask(anchor["model_path"],anchor["template"])
            anchor["anchor_uv_metrics"]=_mask_metrics(anchor["occupancy"]["mask"],anchor["model_uv"]["mask"])
            usable_anchors.append(anchor)
        except Exception as exc:
            anchor["uv_error"]=str(exc)
    anchors=usable_anchors
    if len(anchors)<2:
        return {"ok":False,"message":"Fewer than two BG reference models produced usable body-material UV masks.","assignments":{},"anchors":[]}
    fingerprint=_anchor_fingerprint(anchors)
    assignments={}
    counts={"resolved":0,"ambiguous":0,"special":0,"missing_body":0,"low_information":0}

    for pid,paths in product_sets:
        body=next((Path(p) for p in paths if _slot(p)==1),None)
        if body is None or not body.exists():
            assignments[str(pid)]={"state":"unresolved","method":"BG body texture unavailable","model":None}
            counts["missing_body"]+=1
            continue

        candidate_occ=_foreground_occupancy(body)
        if candidate_occ["low_information"]:
            assignments[str(pid)]={
                "state":"unresolved","method":"BG low-information/solid-color texture","model":None,
                "family_model":None,"template":None,"paintable":None,
                "score":0.0,"margin":0.0,"votes":0,"scores":{},
                "occupancy":{k:v for k,v in candidate_occ.items() if k!="mask"},
                "body_path":str(body),"body_size":body.stat().st_size,"body_mtime_ns":body.stat().st_mtime_ns,
            }
            counts["low_information"]+=1
            continue

        occupancy_scores={}
        uv_scores={}
        uv_metrics={}
        combined={}
        for anchor in anchors:
            model_name=anchor["model"]
            if candidate_occ["usable"] and anchor["occupancy"]["usable"]:
                occupancy_scores[model_name]=_mask_iou(candidate_occ["mask"],anchor["occupancy"]["mask"])
            else:
                occupancy_scores[model_name]=None
            metrics=_mask_metrics(candidate_occ["mask"],anchor["model_uv"]["mask"]) if candidate_occ["usable"] else {"iou":0.0,"dice":0.0,"precision":0.0,"coverage":0.0}
            uv_metrics[model_name]=metrics
            uv_scores[model_name]=metrics["dice"]
            occ=occupancy_scores[model_name]
            combined[model_name]=(MODEL_UV_WEIGHT*uv_scores[model_name] + OCCUPANCY_WEIGHT*(occ if occ is not None else uv_scores[model_name]))

        ranked=sorted(combined.items(),key=lambda x:x[1],reverse=True)
        winner,score80=ranked[0]
        second80=ranked[1][1] if len(ranked)>1 else 0.0
        margin=score80-second80
        vote_count=1
        anchor=next(a for a in anchors if a["model"]==winner)
        clear=(candidate_occ["usable"] and score80>=MIN_WINNER_SCORE and margin>=MIN_MARGIN)

        if not clear:
            state="unresolved";method="BG template family ambiguous";model=None;counts["ambiguous"]+=1
        elif not anchor["paintable"]:
            state="unresolved";method="BG special/non-paintable family";model=None;counts["special"]+=1
        else:
            state="resolved";method="BG background-normalized family match";model=winner;counts["resolved"]+=1

        assignments[str(pid)]={
            "state":state,"method":method,"model":model,
            "family_model":winner,"template":anchor["template"],"paintable":anchor["paintable"],
            "score":round(score80,6),"margin":round(margin,6),"votes":vote_count,
            "scores":{k:round(v,6) for k,v in combined.items()},
            "uv_scores":{k:round(v,6) for k,v in uv_scores.items()},
            "uv_metrics":{k:{mk:round(mv,6) for mk,mv in vals.items()} for k,vals in uv_metrics.items()},
            "occupancy_scores":{k:(round(v,6) if v is not None else None) for k,v in occupancy_scores.items()},
            "occupancy":{k:v for k,v in candidate_occ.items() if k!="mask"},
            "body_path":str(body),"body_size":body.stat().st_size,"body_mtime_ns":body.stat().st_mtime_ns,
        }

    payload={
        "version":CACHE_VERSION,"created":time.strftime("%Y-%m-%d %H:%M:%S"),
        "anchor_fingerprint":fingerprint,
        "thresholds":{
            "quantiles":QUANTILES,"min_winner_score":MIN_WINNER_SCORE,"min_margin":MIN_MARGIN,
            "background_tolerance":BACKGROUND_TOLERANCE,"model_uv_weight":MODEL_UV_WEIGHT,"occupancy_weight":OCCUPANCY_WEIGHT,
            "min_texture_stddev":MIN_TEXTURE_STDDEV,
        },
        "anchors":[{
            "model":a["model"],"model_path":a["model_path"],"template":a["template"],"paintable":a["paintable"],
            "occupancy":{k:v for k,v in a["occupancy"].items() if k!="mask"},
            "model_uv":{"triangles":a["model_uv"]["triangles"],"area":a["model_uv"]["area"],"material_ids":a["model_uv"]["material_ids"]},
            "anchor_uv_metrics":{k:round(v,6) for k,v in a["anchor_uv_metrics"].items()},
        } for a in anchors],
        "counts":counts,"assignments":assignments,
    }
    CACHE_PATH.parent.mkdir(parents=True,exist_ok=True)
    CACHE_PATH.write_text(json.dumps(payload,indent=2),encoding="utf-8")
    _write_report(payload)
    return {"ok":True,**payload}


def _write_report(payload):
    REPORT_ROOT.mkdir(parents=True,exist_ok=True)
    stamp=time.strftime("%Y%m%d-%H%M%S")
    jp=REPORT_ROOT/f"{stamp}-bg-family-analysis.json"
    cp=REPORT_ROOT/f"{stamp}-bg-family-analysis.csv"
    jp.write_text(json.dumps(payload,indent=2),encoding="utf-8")
    with open(cp,"w",newline="",encoding="utf-8-sig") as fh:
        w=csv.writer(fh)
        models=[a["model"] for a in payload.get("anchors",[])]
        w.writerow(["pid","state","method","family_model","template","score","margin","votes","foreground_fraction","border_background_share","stddev",*[f"combined:{m}" for m in models],*[f"occupancy:{m}" for m in models]])
        for pid,x in sorted(payload.get("assignments",{}).items(),key=lambda kv:int(kv[0]) if kv[0].isdigit() else kv[0]):
            occ=x.get("occupancy") or {};w.writerow([pid,x.get("state"),x.get("method"),x.get("family_model"),x.get("template"),x.get("score"),x.get("margin"),x.get("votes"),occ.get("foreground_fraction",""),occ.get("border_background_share",""),occ.get("stddev",""),*[x.get("scores",{}).get(m,"") for m in models],*[x.get("occupancy_scores",{}).get(m,"") for m in models]])
    payload["report_csv"]=str(cp)
    payload["report_json"]=str(jp)


def load_bg_cache():
    try:
        data=json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        return data if data.get("version")==CACHE_VERSION else None
    except Exception:return None


def cached_bg_assignment(pid,body_path=None):
    data=load_bg_cache()
    if not data:return None
    item=(data.get("assignments") or {}).get(str(pid))
    if not item:return None
    if body_path and item.get("body_path"):
        p=Path(body_path)
        try:
            st=p.stat()
            if st.st_size!=item.get("body_size") or st.st_mtime_ns!=item.get("body_mtime_ns"):
                return None
        except OSError:return None
    return item
