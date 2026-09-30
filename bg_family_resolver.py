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


CACHE_VERSION = 12
CACHE_PATH = Path("cache/bg_family_resolution.json")
REPORT_ROOT = Path("reports/bg_family_analysis")
QUANTILES = (0.70, 0.80, 0.85)
MIN_WINNER_SCORE = 0.18
MIN_MARGIN = 0.035
RELAXED_MIN_SCORE = 0.16
RELAXED_MIN_MARGIN = 0.015
CORROBORATED_MIN_SCORE = 0.17
CORROBORATED_MIN_MARGIN = 0.020
SPARSE_MAX_FOREGROUND_FRACTION = 0.15
SPARSE_MIN_BORDER_BACKGROUND_SHARE = 0.90
SPARSE_MAX_OUTLINE_FRACTION = 0.30
BACKGROUND_TOLERANCE = 38
UV_OUTLINE_WEIGHT = 0.55
TEMPLATE_OUTLINE_WEIGHT = 0.30
UV_PRECISION_WEIGHT = 0.10
OCCUPANCY_WEIGHT = 0.05
MIN_BORDER_BACKGROUND_SHARE = 0.15
MIN_FOREGROUND_FRACTION = 0.04
MAX_FOREGROUND_FRACTION = 0.96
MIN_TEXTURE_STDDEV = 5.0
REGRESSION_UV_OUTLINE_WEIGHT = 0.60
REGRESSION_TEMPLATE_OUTLINE_WEIGHT = 0.35
REGRESSION_UV_PRECISION_WEIGHT = 0.05
REGRESSION_MIN_MARGIN = 0.020
REGRESSION_MIN_TEMPLATE_OUTLINE = 0.20
REVIEWED_M002_MIN_TEMPLATE_OUTLINE = 0.20
ANALYSIS_ALGORITHM = "background-removed-uv-outline-v6-reviewed-m002"


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


def _activity_masks(path, quantiles=QUANTILES, size=256, feature_size=64):
    """Return structural edge masks for several thresholds after one decode."""
    im=open_texture_image(path).convert("RGB").resize((size,size),Image.Resampling.LANCZOS)
    r,g,b=im.split()
    er=r.filter(ImageFilter.FIND_EDGES)
    eg=g.filter(ImageFilter.FIND_EDGES)
    eb=b.filter(ImageFilter.FIND_EDGES)
    edge=ImageChops.lighter(ImageChops.lighter(er,eg),eb).filter(ImageFilter.GaussianBlur(1.25))
    masks={}
    for quantile in quantiles:
        threshold=_percentile_threshold(edge,quantile)
        binary=edge.point(lambda p:255 if p>=threshold else 0,"L").filter(ImageFilter.MaxFilter(5))
        small=binary.resize((feature_size,feature_size),Image.Resampling.BOX)
        masks[str(quantile)]=bytes(1 if p>=96 else 0 for p in small.getdata())
    return masks


def _activity_mask(path, quantile=0.80, size=256, feature_size=64):
    return _activity_masks(path,(quantile,),size,feature_size)[str(quantile)]


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

    # Once the background is removed, the outside edge of the remaining
    # artwork is a much cleaner approximation of the actual UV-island layout
    # than RGB edge detection across the designer's artwork.
    cleaned=fg.filter(ImageFilter.MaxFilter(5)).filter(ImageFilter.MinFilter(5))
    dilated=cleaned.filter(ImageFilter.MaxFilter(5))
    eroded=cleaned.filter(ImageFilter.MinFilter(5))
    outline=ImageChops.subtract(dilated,eroded).filter(ImageFilter.MaxFilter(3))

    small=cleaned.resize((feature_size,feature_size),Image.Resampling.BOX)
    outline_small=outline.resize((feature_size,feature_size),Image.Resampling.BOX)
    mask=bytes(1 if p>=96 else 0 for p in small.getdata())
    outline_mask=bytes(1 if p>=48 else 0 for p in outline_small.getdata())
    return {
        "mask":mask,
        "outline_mask":outline_mask,
        "background_rgb":[round(v,2) for v in bg],
        "border_background_share":round(border_share,6),
        "foreground_fraction":round(foreground_fraction,6),
        "outline_fraction":round(sum(outline_mask)/float(feature_size*feature_size),6),
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
    """Build filled UV coverage plus the actual body UV-island boundary topology."""
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

    mask=Image.new("L",(size,size),0)
    boundary=Image.new("L",(size,size),0)
    draw=ImageDraw.Draw(mask)
    boundary_draw=ImageDraw.Draw(boundary)
    triangles=0
    boundary_edges=0
    if not model.lods:
        raise ValueError(f"{Path(model_path).name}: no LODs")

    for mesh in model.lods[0].meshes:
        if mesh.material_index not in material_ids:continue
        verts=mesh.vertices
        edge_counts={}
        edge_points={}
        for i in range(0,len(mesh.indices)-2,3):
            ids=mesh.indices[i:i+3]
            if any(idx>=len(verts) or verts[idx].uv0 is None for idx in ids):continue
            pts=[]
            for idx in ids:
                u,v=verts[idx].uv0
                pts.append((round(u*(size-1)),round((1.0-v)*(size-1))))
            draw.polygon(pts,fill=255)
            triangles+=1
            for a,b in ((0,1),(1,2),(2,0)):
                key=tuple(sorted((ids[a],ids[b])))
                edge_counts[key]=edge_counts.get(key,0)+1
                edge_points[key]=(pts[a],pts[b])
        for key,count in edge_counts.items():
            if count==1:
                boundary_draw.line(edge_points[key],fill=255,width=2)
                boundary_edges+=1

    # Give a texture edge a few pixels of tolerance around the exact UV seam.
    boundary=boundary.filter(ImageFilter.MaxFilter(5))
    small=mask.resize((feature_size,feature_size),Image.Resampling.BOX)
    boundary_small=boundary.resize((feature_size,feature_size),Image.Resampling.BOX)
    bits=bytes(1 if p>=64 else 0 for p in small.getdata())
    boundary_bits=bytes(1 if p>=48 else 0 for p in boundary_small.getdata())
    area=sum(bits)
    boundary_area=sum(boundary_bits)
    if triangles==0 or area==0:
        raise ValueError(f"{Path(model_path).name}: body material has no usable UV triangles")
    if boundary_edges==0 or boundary_area==0:
        raise ValueError(f"{Path(model_path).name}: body material has no usable UV island boundaries")
    return {
        "mask":bits,"boundary_mask":boundary_bits,
        "triangles":triangles,"area":area,
        "boundary_edges":boundary_edges,"boundary_area":boundary_area,
        "material_ids":material_ids,
    }


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
    payload="\n".join(sorted(
        f"{x['model']}|{x['paintable']}|{_file_token(x['path'])}|{_file_token(Path(x['model_path']))}"
        for x in anchors
    ))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _analysis_fingerprint(anchor_fingerprint):
    settings={
        "algorithm":ANALYSIS_ALGORITHM,
        "background_tolerance":BACKGROUND_TOLERANCE,
        "uv_outline_weight":UV_OUTLINE_WEIGHT,
        "template_outline_weight":TEMPLATE_OUTLINE_WEIGHT,
        "uv_precision_weight":UV_PRECISION_WEIGHT,
        "occupancy_weight":OCCUPANCY_WEIGHT,
        "min_winner_score":MIN_WINNER_SCORE,
        "min_margin":MIN_MARGIN,
        "relaxed_min_score":RELAXED_MIN_SCORE,
        "relaxed_min_margin":RELAXED_MIN_MARGIN,
        "corroborated_min_score":CORROBORATED_MIN_SCORE,
        "corroborated_min_margin":CORROBORATED_MIN_MARGIN,
        "regression_uv_outline_weight":REGRESSION_UV_OUTLINE_WEIGHT,
        "regression_template_outline_weight":REGRESSION_TEMPLATE_OUTLINE_WEIGHT,
        "regression_uv_precision_weight":REGRESSION_UV_PRECISION_WEIGHT,
        "regression_min_margin":REGRESSION_MIN_MARGIN,
        "regression_min_template_outline":REGRESSION_MIN_TEMPLATE_OUTLINE,
        "reviewed_m002_min_template_outline":REVIEWED_M002_MIN_TEMPLATE_OUTLINE,
        "min_border_background_share":MIN_BORDER_BACKGROUND_SHARE,
        "min_foreground_fraction":MIN_FOREGROUND_FRACTION,
        "max_foreground_fraction":MAX_FOREGROUND_FRACTION,
        "min_texture_stddev":MIN_TEXTURE_STDDEV,
    }
    encoded=json.dumps(settings,sort_keys=True,separators=(",",":"))
    return hashlib.sha256(f"{anchor_fingerprint}\n{encoded}".encode("utf-8")).hexdigest()


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

    The dominant background is removed first. The outline of the remaining
    product artwork is then compared directly with the decoded model's body UV
    island boundaries and with the stock template's background-removed outline.
    Filled occupancy is only secondary evidence. Weak/conflicting matches remain
    unresolved rather than being forced into a family.
    """
    anchors=_references(product_sets,models,rules)
    if len(anchors)<2:
        return {"ok":False,"message":"Fewer than two verified BG reference templates were found in the indexed client folder.","assignments":{},"anchors":[]}

    usable_anchors=[]
    for anchor in anchors:
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
    analysis_fingerprint=_analysis_fingerprint(fingerprint)
    previous=load_bg_cache()
    reuse_previous=bool(previous and previous.get("analysis_fingerprint")==analysis_fingerprint)
    previous_assignments=(previous.get("assignments") or {}) if reuse_previous else {}
    assignments={}
    counts={"resolved":0,"resolved_strict":0,"resolved_outline_agreement":0,"resolved_corroborated":0,"resolved_regression_proposal":0,"resolved_reviewed_m002":0,"ambiguous":0,"special":0,"missing_body":0,"low_information":0}
    reused=recomputed=0

    def count_assignment(item):
        method=item.get("method") or ""
        if item.get("state")=="resolved":
            counts["resolved"]+=1
            tier=item.get("confidence_tier")
            if tier=="strict":counts["resolved_strict"]+=1
            elif tier=="outline-agreement":counts["resolved_outline_agreement"]+=1
            elif tier=="corroborated":counts["resolved_corroborated"]+=1
            elif tier=="regression-proposal":counts["resolved_regression_proposal"]+=1
            elif tier=="reviewed-m002-template":counts["resolved_reviewed_m002"]+=1
        elif method=="BG special/non-paintable family":counts["special"]+=1
        elif method=="BG low-information/solid-color texture":counts["low_information"]+=1
        elif method=="BG body texture unavailable":counts["missing_body"]+=1
        else:counts["ambiguous"]+=1

    for pid,paths in product_sets:
        body=next((Path(p) for p in paths if _slot(p)==1),None)
        if body is None or not body.exists():
            assignments[str(pid)]={"state":"unresolved","method":"BG body texture unavailable","model":None}
            counts["missing_body"]+=1
            continue

        body_stat=body.stat()
        prior=previous_assignments.get(str(pid))
        if prior and prior.get("body_size")==body_stat.st_size and prior.get("body_mtime_ns")==body_stat.st_mtime_ns:
            assignments[str(pid)]=prior
            count_assignment(prior)
            reused+=1
            continue

        recomputed+=1
        candidate_occ=_foreground_occupancy(body)
        if candidate_occ["low_information"]:
            assignments[str(pid)]={
                "state":"unresolved","method":"BG low-information/solid-color texture","model":None,
                "family_model":None,"template":None,"paintable":None,
                "score":0.0,"margin":0.0,"votes":0,"scores":{},
                "occupancy":{k:v for k,v in candidate_occ.items() if k not in ("mask","outline_mask")},
                "body_path":str(body),"body_size":body_stat.st_size,"body_mtime_ns":body_stat.st_mtime_ns,
            }
            counts["low_information"]+=1
            continue

        occupancy_scores={}
        uv_scores={}
        uv_metrics={}
        uv_outline_scores={}
        template_outline_scores={}
        uv_precision_scores={}
        combined={}
        for anchor in anchors:
            model_name=anchor["model"]
            if candidate_occ["usable"] and anchor["occupancy"]["usable"]:
                occupancy_scores[model_name]=_mask_iou(candidate_occ["mask"],anchor["occupancy"]["mask"])
                template_outline_scores[model_name]=_mask_metrics(
                    candidate_occ["outline_mask"],anchor["occupancy"]["outline_mask"]
                )["dice"]
            else:
                occupancy_scores[model_name]=None
                template_outline_scores[model_name]=0.0

            fill_metrics=_mask_metrics(candidate_occ["mask"],anchor["model_uv"]["mask"]) if candidate_occ["usable"] else {"iou":0.0,"dice":0.0,"precision":0.0,"coverage":0.0}
            uv_metrics[model_name]=fill_metrics
            uv_scores[model_name]=fill_metrics["dice"]
            uv_precision_scores[model_name]=fill_metrics["precision"]

            if candidate_occ["usable"]:
                outline_metrics=_mask_metrics(candidate_occ["outline_mask"],anchor["model_uv"]["boundary_mask"])
                # Favor model-boundary coverage slightly: a product outline should
                # trace the UV islands, while small missing/painted-over sections are
                # tolerated.
                uv_outline_scores[model_name]=(0.60*outline_metrics["coverage"] + 0.40*outline_metrics["precision"])
            else:
                uv_outline_scores[model_name]=0.0

            occ=occupancy_scores[model_name]
            occ_score=occ if occ is not None else 0.0
            combined[model_name]=(
                UV_OUTLINE_WEIGHT*uv_outline_scores[model_name]
                + TEMPLATE_OUTLINE_WEIGHT*template_outline_scores[model_name]
                + UV_PRECISION_WEIGHT*uv_precision_scores[model_name]
                + OCCUPANCY_WEIGHT*occ_score
            )

        ranked=sorted(combined.items(),key=lambda x:x[1],reverse=True)
        winner,score80=ranked[0]
        second80=ranked[1][1] if len(ranked)>1 else 0.0
        margin=score80-second80

        # Independent evidence should agree before Inspector commits to a model.
        # Start with the proven 14/14 strict gate, then admit two cautious
        # "yes, but only with corroboration" paths so coverage can grow without
        # returning to confident Bronco false positives.
        modalities=[uv_outline_scores,template_outline_scores]
        if candidate_occ["usable"]:
            modalities.extend([uv_precision_scores,{k:(v if v is not None else -1.0) for k,v in occupancy_scores.items()}])
        vote_count=sum(1 for scoreset in modalities if max(scoreset,key=scoreset.get)==winner)
        uv_winner=max(uv_outline_scores,key=uv_outline_scores.get)
        template_winner=max(template_outline_scores,key=template_outline_scores.get)
        primary_agree=(uv_winner==winner and template_winner==winner)

        # Reviewed validation showed that winner margin is a poor confidence gate
        # for closely related BG families. When the independently derived model-UV
        # outline and stock-template outline choose the same family, accept that
        # agreement without a minimum separation margin. Keep a conservative sparse
        # evidence hold for textures like known regression PID 216317831, where a
        # mostly-background image can accidentally resemble the wrong family.
        sparse_evidence=(
            candidate_occ["foreground_fraction"]<=SPARSE_MAX_FOREGROUND_FRACTION and
            candidate_occ["border_background_share"]>=SPARSE_MIN_BORDER_BACKGROUND_SHARE and
            candidate_occ["outline_fraction"]<=SPARSE_MAX_OUTLINE_FRACTION
        )
        strict_clear=(score80>=MIN_WINNER_SCORE and margin>=MIN_MARGIN and vote_count>=2 and not sparse_evidence)
        outline_clear=(primary_agree and score80>=RELAXED_MIN_SCORE and not sparse_evidence)
        corroborated_clear=(vote_count>=3 and score80>=CORROBORATED_MIN_SCORE and margin>=CORROBORATED_MIN_MARGIN and not sparse_evidence)

        paintable_models=[a["model"] for a in anchors if a["paintable"]]
        regression_scores={
            name:(
                REGRESSION_UV_OUTLINE_WEIGHT*uv_outline_scores.get(name,0.0)
                + REGRESSION_TEMPLATE_OUTLINE_WEIGHT*template_outline_scores.get(name,0.0)
                + REGRESSION_UV_PRECISION_WEIGHT*uv_precision_scores.get(name,0.0)
            )
            for name in paintable_models
        }
        regression_ranked=sorted(regression_scores.items(),key=lambda x:x[1],reverse=True)
        regression_winner=regression_ranked[0][0] if regression_ranked else None
        regression_score=regression_ranked[0][1] if regression_ranked else 0.0
        regression_second=regression_ranked[1][1] if len(regression_ranked)>1 else 0.0
        regression_margin=regression_score-regression_second
        regression_template_support=template_outline_scores.get(regression_winner,0.0) if regression_winner else 0.0
        regression_clear=(
            not sparse_evidence and regression_winner is not None and
            regression_margin>=REGRESSION_MIN_MARGIN and
            regression_template_support>=REGRESSION_MIN_TEMPLATE_OUTLINE
        )
        reviewed_m002_template_support=template_outline_scores.get("m002bg.model",0.0)
        reviewed_m002_clear=(
            not sparse_evidence and winner=="m002bg.model" and
            reviewed_m002_template_support>=REVIEWED_M002_MIN_TEMPLATE_OUTLINE
        )

        if strict_clear:
            confidence_tier="strict"
        elif outline_clear:
            confidence_tier="outline-agreement"
        elif corroborated_clear:
            confidence_tier="corroborated"
        elif regression_clear:
            confidence_tier="regression-proposal"
            winner=regression_winner
            score80=combined.get(winner,0.0)
            others=[v for name,v in combined.items() if name!=winner]
            margin=score80-(max(others) if others else 0.0)
            vote_count=sum(1 for scoreset in modalities if max(scoreset,key=scoreset.get)==winner)
            primary_agree=(uv_winner==winner and template_winner==winner)
        elif reviewed_m002_clear:
            confidence_tier="reviewed-m002-template"
        else:
            confidence_tier=None

        anchor=next(a for a in anchors if a["model"]==winner)
        clear=confidence_tier is not None

        # Diagnostic acceptance math. These values are exported for every PID so
        # reviewed samples can show exactly which gate accepted/rejected it.
        strict_score_gap=score80-MIN_WINNER_SCORE
        strict_margin_gap=margin-MIN_MARGIN
        relaxed_score_gap=score80-RELAXED_MIN_SCORE
        relaxed_margin_gap=margin-RELAXED_MIN_MARGIN
        corroborated_score_gap=score80-CORROBORATED_MIN_SCORE
        corroborated_margin_gap=margin-CORROBORATED_MIN_MARGIN

        if strict_clear:
            decision_reason="accepted: strict score/margin + 2 votes"
        elif outline_clear:
            decision_reason="accepted: UV + template outlines agree; margin diagnostic only"
        elif corroborated_clear:
            decision_reason="accepted: 3+ independent signals corroborate"
        elif confidence_tier=="regression-proposal":
            decision_reason=f"accepted: validated 60/35/5 paintable proposal margin {regression_margin:.4f} >= {REGRESSION_MIN_MARGIN:.4f}"
        elif confidence_tier=="reviewed-m002-template":
            decision_reason=f"accepted: reviewed m002 winner + m002 template support {reviewed_m002_template_support:.4f} >= {REVIEWED_M002_MIN_TEMPLATE_OUTLINE:.4f}"
        else:
            blockers=[]
            if sparse_evidence:
                blockers.append("sparse evidence hold")
            if score80<RELAXED_MIN_SCORE:
                blockers.append(f"score {score80:.4f} < relaxed {RELAXED_MIN_SCORE:.4f}")
            if margin<RELAXED_MIN_MARGIN:
                blockers.append(f"margin {margin:.4f} < legacy relaxed {RELAXED_MIN_MARGIN:.4f} (diagnostic only when primary outlines agree)")
            if not primary_agree:
                blockers.append(f"primary outlines disagree ({uv_winner} vs {template_winner})")
            if vote_count<3:
                blockers.append(f"only {vote_count} corroborating votes")
            decision_reason="rejected: " + ("; ".join(blockers) if blockers else "no acceptance tier satisfied")

        if not clear:
            state="unresolved";method="BG template family ambiguous";model=None;counts["ambiguous"]+=1
        elif not anchor["paintable"]:
            state="unresolved";method="BG special/non-paintable family";model=None;counts["special"]+=1
        else:
            state="resolved";method=("BG validated regression proposal" if confidence_tier=="regression-proposal" else ("BG reviewed m002 template support" if confidence_tier=="reviewed-m002-template" else "BG background-removed UV outline match"));model=winner;counts["resolved"]+=1
            if confidence_tier=="strict":counts["resolved_strict"]+=1
            elif confidence_tier=="outline-agreement":counts["resolved_outline_agreement"]+=1
            elif confidence_tier=="corroborated":counts["resolved_corroborated"]+=1
            elif confidence_tier=="regression-proposal":counts["resolved_regression_proposal"]+=1
            elif confidence_tier=="reviewed-m002-template":counts["resolved_reviewed_m002"]+=1

        # Evidence confidence is separate from winner margin because related
        # UV families can be genuine near-ties.
        evidence_parts=[
            max(0.0,min(1.0,uv_outline_scores.get(winner,0.0))),
            max(0.0,min(1.0,template_outline_scores.get(winner,0.0))),
            max(0.0,min(1.0,uv_precision_scores.get(winner,0.0))),
            max(0.0,min(1.0,vote_count/4.0)),
            1.0 if primary_agree else 0.0,
        ]
        evidence_confidence=sum(evidence_parts)/len(evidence_parts)
        evidence_band="strong" if evidence_confidence>=0.72 else ("moderate" if evidence_confidence>=0.60 else "limited")

        assignments[str(pid)]={
            "state":state,"method":method,"model":model,
            "family_model":winner,"template":anchor["template"],"paintable":anchor["paintable"],
            "score":round(score80,6),"margin":round(margin,6),"votes":vote_count,
            "confidence_tier":confidence_tier,"evidence_confidence":round(evidence_confidence,6),"evidence_band":evidence_band,"sparse_evidence":sparse_evidence,"primary_agree":primary_agree,
            "uv_winner":uv_winner,"template_winner":template_winner,
            "regression_family":regression_winner,"regression_score":round(regression_score,6),"regression_margin":round(regression_margin,6),
            "regression_template_support":round(regression_template_support,6),
            "reviewed_m002_template_support":round(reviewed_m002_template_support,6),
            "regression_scores":{k:round(v,6) for k,v in regression_scores.items()},
            "decision_reason":decision_reason,
            "strict_score_gap":round(strict_score_gap,6),"strict_margin_gap":round(strict_margin_gap,6),
            "relaxed_score_gap":round(relaxed_score_gap,6),"relaxed_margin_gap":round(relaxed_margin_gap,6),
            "corroborated_score_gap":round(corroborated_score_gap,6),"corroborated_margin_gap":round(corroborated_margin_gap,6),
            "scores":{k:round(v,6) for k,v in combined.items()},
            "uv_scores":{k:round(v,6) for k,v in uv_scores.items()},
            "uv_metrics":{k:{mk:round(mv,6) for mk,mv in vals.items()} for k,vals in uv_metrics.items()},
            "uv_outline_scores":{k:round(v,6) for k,v in uv_outline_scores.items()},
            "template_outline_scores":{k:round(v,6) for k,v in template_outline_scores.items()},
            "uv_precision_scores":{k:round(v,6) for k,v in uv_precision_scores.items()},
            "occupancy_scores":{k:(round(v,6) if v is not None else None) for k,v in occupancy_scores.items()},
            "occupancy":{k:v for k,v in candidate_occ.items() if k not in ("mask","outline_mask")},
            "body_path":str(body),"body_size":body.stat().st_size,"body_mtime_ns":body.stat().st_mtime_ns,
        }

    payload={
        "version":CACHE_VERSION,"created":time.strftime("%Y-%m-%d %H:%M:%S"),
        "anchor_fingerprint":fingerprint,
        "analysis_fingerprint":analysis_fingerprint,
        "algorithm":ANALYSIS_ALGORITHM,
        "thresholds":{
            "quantiles":QUANTILES,"min_winner_score":MIN_WINNER_SCORE,"min_margin":MIN_MARGIN,
            "relaxed_min_score":RELAXED_MIN_SCORE,"relaxed_min_margin":RELAXED_MIN_MARGIN,
            "corroborated_min_score":CORROBORATED_MIN_SCORE,"corroborated_min_margin":CORROBORATED_MIN_MARGIN,
            "regression_uv_outline_weight":REGRESSION_UV_OUTLINE_WEIGHT,
            "regression_template_outline_weight":REGRESSION_TEMPLATE_OUTLINE_WEIGHT,
            "regression_uv_precision_weight":REGRESSION_UV_PRECISION_WEIGHT,
            "regression_min_margin":REGRESSION_MIN_MARGIN,
            "regression_min_template_outline":REGRESSION_MIN_TEMPLATE_OUTLINE,
            "reviewed_m002_min_template_outline":REVIEWED_M002_MIN_TEMPLATE_OUTLINE,
            "background_tolerance":BACKGROUND_TOLERANCE,
            "sparse_max_foreground_fraction":SPARSE_MAX_FOREGROUND_FRACTION,
            "sparse_min_border_background_share":SPARSE_MIN_BORDER_BACKGROUND_SHARE,
            "sparse_max_outline_fraction":SPARSE_MAX_OUTLINE_FRACTION,
            "uv_outline_weight":UV_OUTLINE_WEIGHT,"template_outline_weight":TEMPLATE_OUTLINE_WEIGHT,
            "uv_precision_weight":UV_PRECISION_WEIGHT,"occupancy_weight":OCCUPANCY_WEIGHT,
            "min_texture_stddev":MIN_TEXTURE_STDDEV,
        },
        "anchors":[{
            "model":a["model"],"model_path":a["model_path"],"template":a["template"],"paintable":a["paintable"],
            "occupancy":{k:v for k,v in a["occupancy"].items() if k not in ("mask","outline_mask")},
            "model_uv":{"triangles":a["model_uv"]["triangles"],"area":a["model_uv"]["area"],"boundary_edges":a["model_uv"]["boundary_edges"],"boundary_area":a["model_uv"]["boundary_area"],"material_ids":a["model_uv"]["material_ids"]},
            "anchor_uv_metrics":{k:round(v,6) for k,v in a["anchor_uv_metrics"].items()},
        } for a in anchors],
        "counts":counts,"reused":reused,"recomputed":recomputed,"assignments":assignments,
    }
    CACHE_PATH.parent.mkdir(parents=True,exist_ok=True)
    CACHE_PATH.write_text(json.dumps(payload,indent=2),encoding="utf-8")
    _BG_CACHE_MEMO["token"]=None;_BG_CACHE_MEMO["data"]=None
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
        w.writerow([
            "pid","state","method","confidence_tier","evidence_confidence","evidence_band","sparse_evidence","decision_reason",
            "primary_agree","uv_winner","template_winner","family_model","template",
            "score","margin","votes","regression_family","regression_score","regression_margin",
            "strict_score_gap","strict_margin_gap",
            "relaxed_score_gap","relaxed_margin_gap",
            "corroborated_score_gap","corroborated_margin_gap",
            "foreground_fraction","outline_fraction","border_background_share","stddev",
            *[f"combined:{m}" for m in models],
            *[f"uv_outline:{m}" for m in models],
            *[f"template_outline:{m}" for m in models],
            *[f"uv_precision:{m}" for m in models],
            *[f"uv_dice:{m}" for m in models],
            *[f"occupancy:{m}" for m in models]
        ])
        for pid,x in sorted(payload.get("assignments",{}).items(),key=lambda kv:int(kv[0]) if kv[0].isdigit() else kv[0]):
            occ=x.get("occupancy") or {}
            w.writerow([
                pid,x.get("state"),x.get("method"),x.get("confidence_tier",""),x.get("evidence_confidence",""),x.get("evidence_band",""),x.get("sparse_evidence",""),x.get("decision_reason",""),
                x.get("primary_agree",""),x.get("uv_winner",""),x.get("template_winner",""),
                x.get("family_model"),x.get("template"),x.get("score"),x.get("margin"),x.get("votes"),
                x.get("regression_family",""),x.get("regression_score",""),x.get("regression_margin",""),
                x.get("strict_score_gap",""),x.get("strict_margin_gap",""),
                x.get("relaxed_score_gap",""),x.get("relaxed_margin_gap",""),
                x.get("corroborated_score_gap",""),x.get("corroborated_margin_gap",""),
                occ.get("foreground_fraction",""),occ.get("outline_fraction",""),
                occ.get("border_background_share",""),occ.get("stddev",""),
                *[x.get("scores",{}).get(m,"") for m in models],
                *[x.get("uv_outline_scores",{}).get(m,"") for m in models],
                *[x.get("template_outline_scores",{}).get(m,"") for m in models],
                *[x.get("uv_precision_scores",{}).get(m,"") for m in models],
                *[x.get("uv_scores",{}).get(m,"") for m in models],
                *[x.get("occupancy_scores",{}).get(m,"") for m in models]
            ])
    payload["report_csv"]=str(cp)
    payload["report_json"]=str(jp)


_BG_CACHE_MEMO={"token":None,"data":None}

def load_bg_cache():
    """Load the BG analysis cache once per file version instead of once per PID."""
    try:
        st=CACHE_PATH.stat()
        token=(st.st_size,st.st_mtime_ns,CACHE_VERSION)
        if _BG_CACHE_MEMO["token"]==token:
            return _BG_CACHE_MEMO["data"]
        data=json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        if data.get("version")!=CACHE_VERSION:data=None
        _BG_CACHE_MEMO["token"]=token;_BG_CACHE_MEMO["data"]=data
        return data
    except Exception:
        _BG_CACHE_MEMO["token"]=None;_BG_CACHE_MEMO["data"]=None
        return None


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
