from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path

from there_model_decoder import decode_model


FORENSIC_VERSION = 1
CACHE_DIR = Path("cache/model_forensics")


def _q(v, places=5):
    return round(float(v), places)


def _digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(",",":")).encode("utf-8")).hexdigest()


def _triangle_geometry(mesh):
    out=[]
    for i in range(0,len(mesh.indices)-2,3):
        ids=mesh.indices[i:i+3]
        if any(idx>=len(mesh.vertices) for idx in ids):continue
        tri=tuple(sorted(tuple(_q(x) for x in mesh.vertices[idx].position) for idx in ids))
        out.append(tri)
    return sorted(out)


def _triangle_uv(mesh):
    out=[]
    for i in range(0,len(mesh.indices)-2,3):
        ids=mesh.indices[i:i+3]
        if any(idx>=len(mesh.vertices) or mesh.vertices[idx].uv0 is None for idx in ids):continue
        tri=tuple(sorted((_q(mesh.vertices[idx].uv0[0]),_q(mesh.vertices[idx].uv0[1])) for idx in ids))
        out.append(tri)
    return sorted(out)


def model_facts(path,force=False):
    """Facts measured directly from a There model. No fuzzy thresholds."""
    p=Path(path);st=p.stat();key=_digest([str(p.resolve()),st.st_size,st.st_mtime_ns,FORENSIC_VERSION])[:24]
    cache=CACHE_DIR/(key+".json")
    if cache.exists() and not force:return json.loads(cache.read_text(encoding="utf-8"))
    m=decode_model(p);lods=[]
    for li,lod in enumerate(m.lods):
        geom_groups=[];uv_groups=[];vertices=triangles=0
        for mesh in lod.meshes:
            vertices+=len(mesh.vertices);triangles+=len(mesh.indices)//3
            geom_groups.append({"material":mesh.material_index,"triangles":_triangle_geometry(mesh)})
            uv_groups.append({"material":mesh.material_index,"triangles":_triangle_uv(mesh)})
        geom=sorted(t for g in geom_groups for t in g["triangles"])
        uv=sorted(t for g in uv_groups for t in g["triangles"])
        lods.append({"lod":li,"mesh_count":len(lod.meshes),"vertex_count":vertices,"triangle_count":triangles,
                     "geometry":_digest(geom),"geometry_material":_digest(geom_groups),
                     "uv":_digest(uv),"uv_material":_digest(uv_groups)})
    collision=None
    if m.collision:
        verts=sorted(tuple(_q(x) for x in v) for v in m.collision.vertices)
        collision={"vertices":len(m.collision.vertices),"polygons":len(m.collision.polygons),
                   "fingerprint":_digest({"vertices":verts,"polygons":sorted(tuple(sorted(poly)) for poly in m.collision.polygons)})}
    data={"version":FORENSIC_VERSION,"path":str(p.resolve()),"som_version":m.version,"lod_count":len(m.lods),
          "material_count":len(m.materials),"node_count":len(m.nodes),"lods":lods,"collision":collision}
    cache.parent.mkdir(parents=True,exist_ok=True);cache.write_text(json.dumps(data,indent=2),encoding="utf-8");return data


def analyze_model_rows(rows):
    """Return only relationships proven by equal deterministic fingerprints."""
    decoded=[];unsupported=[]
    for row in rows:
        try:decoded.append((dict(row),model_facts(row["path"])))
        except Exception as exc:unsupported.append({"filename":row["filename"],"path":row["path"],"reason":str(exc)})
    buckets=defaultdict(list)
    for row,fact in decoded:
        for lod in fact["lods"]:
            for key,label in (("geometry","Exact geometry"),("geometry_material","Exact geometry + material assignment"),
                              ("uv","Exact UV layout"),("uv_material","Exact UV layout + material assignment")):
                buckets[(lod["lod"],key,label,lod[key])].append((row,fact,lod))
        if fact["collision"]:
            buckets[(-1,"collision","Exact collision geometry",fact["collision"]["fingerprint"])].append((row,fact,None))
    rel=[];seen=set()
    for (lod,key,label,digest),members in buckets.items():
        if len(members)<2:continue
        for i in range(len(members)):
            for j in range(i+1,len(members)):
                a=members[i][0];b=members[j][0];pair=tuple(sorted((a["path"],b["path"])))
                unique=(pair,lod,key)
                if unique in seen:continue
                seen.add(unique)
                rel.append({"asset_a":a["path"],"asset_b":b["path"],"filename_a":a["filename"],"filename_b":b["filename"],
                            "lod":lod,"evidence":label,"fingerprint":digest})
    # Exact file duplicates come from the indexed SHA and do not require decoding.
    sha=defaultdict(list)
    for row in rows:
        if row["sha256"]:sha[row["sha256"]].append(dict(row))
    for digest,members in sha.items():
        if len(members)<2:continue
        for i in range(len(members)):
            for j in range(i+1,len(members)):
                a,b=members[i],members[j];rel.append({"asset_a":a["path"],"asset_b":b["path"],"filename_a":a["filename"],"filename_b":b["filename"],
                                                     "lod":-1,"evidence":"Exact file duplicate","fingerprint":digest})
    rel.sort(key=lambda x:(x["filename_a"].lower(),x["filename_b"].lower(),x["lod"],x["evidence"]))
    counts=defaultdict(int)
    for x in rel:counts[x["evidence"]]+=1
    return {"models":len(rows),"decoded":len(decoded),"unsupported":unsupported,"relationships":rel,"counts":dict(counts)}
