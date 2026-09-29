from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path

from there_model_decoder import decode_model


FORENSIC_VERSION = 2
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
                     "uv":_digest(uv),"uv_material":_digest(uv_groups),
                     "geometry_triangles":geom,"uv_triangles":uv})
    collision=None
    if m.collision:
        verts=sorted(tuple(_q(x) for x in v) for v in m.collision.vertices)
        collision={"vertices":len(m.collision.vertices),"polygons":len(m.collision.polygons),
                   "fingerprint":_digest({"vertices":verts,"polygons":sorted(tuple(sorted(poly)) for poly in m.collision.polygons)})}
    data={"version":FORENSIC_VERSION,"path":str(p.resolve()),"som_version":m.version,"lod_count":len(m.lods),
          "material_count":len(m.materials),"node_count":len(m.nodes),"lods":lods,"collision":collision}
    cache.parent.mkdir(parents=True,exist_ok=True);cache.write_text(json.dumps(data,indent=2),encoding="utf-8");return data


def _overlap(a,b):
    """Set intersection measurements; returned percentages are descriptive, not confidence."""
    sa=set(tuple(tuple(p) for p in tri) for tri in a);sb=set(tuple(tuple(p) for p in tri) for tri in b)
    shared=len(sa & sb)
    return shared,len(sa),len(sb),(100.0*shared/len(sa) if sa else 0.0),(100.0*shared/len(sb) if sb else 0.0)


def analyze_model_rows(rows):
    """Exact evidence plus measured structural overlap. No confidence thresholds."""
    decoded=[];unsupported=[]
    for row in rows:
        try:decoded.append((dict(row),model_facts(row["path"])))
        except Exception as exc:unsupported.append({"filename":row["filename"],"path":row["path"],"reason":str(exc)})
    relationships=[];counts=defaultdict(int)
    # Every decoded pair is measured. Nothing is promoted or hidden by an arbitrary cutoff.
    for i in range(len(decoded)):
        for j in range(i+1,len(decoded)):
            ra,fa=decoded[i];rb,fb=decoded[j]
            common=min(len(fa["lods"]),len(fb["lods"]))
            for li in range(common):
                a=fa["lods"][li];b=fb["lods"][li]
                gs,ga,gb,gpa,gpb=_overlap(a["geometry_triangles"],b["geometry_triangles"])
                us,ua,ub,upa,upb=_overlap(a["uv_triangles"],b["uv_triangles"])
                exact=[]
                if a["geometry"]==b["geometry"]:exact.append("Exact geometry")
                if a["geometry_material"]==b["geometry_material"]:exact.append("Exact geometry + material assignment")
                if a["uv"]==b["uv"]:exact.append("Exact UV layout")
                if a["uv_material"]==b["uv_material"]:exact.append("Exact UV layout + material assignment")
                if gs or us or exact:
                    evidence="; ".join(exact) if exact else "Measured structural overlap"
                    detail=f"Geometry: {gs:,} shared of A {ga:,} / B {gb:,} ({gpa:.1f}% A, {gpb:.1f}% B) • UV: {us:,} shared of A {ua:,} / B {ub:,} ({upa:.1f}% A, {upb:.1f}% B)"
                    relationships.append({"asset_a":ra["path"],"asset_b":rb["path"],"filename_a":ra["filename"],"filename_b":rb["filename"],
                        "lod":li,"evidence":evidence,"fingerprint":a["geometry"] if exact else "", "details":detail,
                        "geometry_shared":gs,"geometry_a":ga,"geometry_b":gb,"uv_shared":us,"uv_a":ua,"uv_b":ub})
                    counts[evidence]+=1
            ca=fa.get("collision");cb=fb.get("collision")
            if ca and cb and ca["fingerprint"]==cb["fingerprint"]:
                relationships.append({"asset_a":ra["path"],"asset_b":rb["path"],"filename_a":ra["filename"],"filename_b":rb["filename"],
                    "lod":-1,"evidence":"Exact collision geometry","fingerprint":ca["fingerprint"],"details":f"Collision identical: {ca['vertices']:,} vertices, {ca['polygons']:,} polygons.",
                    "geometry_shared":0,"geometry_a":0,"geometry_b":0,"uv_shared":0,"uv_a":0,"uv_b":0})
                counts["Exact collision geometry"]+=1
    sha=defaultdict(list)
    for row in rows:
        if row["sha256"]:sha[row["sha256"]].append(dict(row))
    for digest,members in sha.items():
        if len(members)<2:continue
        for i in range(len(members)):
            for j in range(i+1,len(members)):
                a,b=members[i],members[j]
                relationships.append({"asset_a":a["path"],"asset_b":b["path"],"filename_a":a["filename"],"filename_b":b["filename"],
                    "lod":-1,"evidence":"Exact file duplicate","fingerprint":digest,"details":"Indexed SHA-256 is identical.",
                    "geometry_shared":0,"geometry_a":0,"geometry_b":0,"uv_shared":0,"uv_a":0,"uv_b":0})
                counts["Exact file duplicate"]+=1
    relationships.sort(key=lambda x:(x["filename_a"].lower(),x["filename_b"].lower(),x["lod"],x["evidence"]))
    return {"models":len(rows),"decoded":len(decoded),"unsupported":unsupported,"relationships":relationships,"counts":dict(counts)}
