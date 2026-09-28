from __future__ import annotations
import hashlib, json, math, subprocess, time
from pathlib import Path
from config import PROJECT_DIR
from model_converter import find_blender
from there_model_decoder import decode_model, export_obj
from there_texture_decoder import blender_texture_path

CACHE_DIR=PROJECT_DIR/"cache"/"model_thumbnails"
WORK_DIR=PROJECT_DIR/"cache"/"thumbnail_work"
FAILURE_LOG=PROJECT_DIR/"cache"/"thumbnail_failures.jsonl"
METADATA_DIR=PROJECT_DIR/"cache"/"model_thumbnail_meta"
RENDER_VERSION=2
VARIANT_RENDER_VERSION=6
VARIANT_CACHE_DIR=PROJECT_DIR/"cache"/"model_variants"

def thumbnail_key(model_path):
    p=Path(model_path)
    st=p.stat()
    return hashlib.sha256(f"{p.resolve()}|{st.st_size}|{st.st_mtime_ns}".encode()).hexdigest()[:24]

def thumbnail_path(model_path,size=512):
    return CACHE_DIR/f"{thumbnail_key(model_path)}_{size}.png"

def variant_key(model_path,texture_paths,size=512):
    p=Path(model_path);parts=[str(p.resolve()),str(p.stat().st_mtime_ns),str(size),f"variant-v{VARIANT_RENDER_VERSION}"]
    for t in sorted(map(str,texture_paths or [])):
        tp=Path(t)
        try:parts.extend([str(tp.resolve()),str(tp.stat().st_mtime_ns)])
        except Exception:parts.append(t)
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:28]

def variant_thumbnail_path(model_path,texture_paths,size=512):
    return VARIANT_CACHE_DIR/f"{variant_key(model_path,texture_paths,size)}_{size}.png"

def cached_variant_thumbnail(model_path,texture_paths,size=512):
    p=variant_thumbnail_path(model_path,texture_paths,size)
    return p if p.exists() else None

def remove_cached_variant(model_path,texture_paths,size=512):
    p=variant_thumbnail_path(model_path,texture_paths,size);existed=p.exists();p.unlink(missing_ok=True);return existed

def cached_thumbnail(model_path,size=512):
    p=thumbnail_path(model_path,size)
    return p if p.exists() else None

def metadata_path(model_path):
    return METADATA_DIR/f"{thumbnail_key(model_path)}.json"

def render_metadata(model_path):
    p=metadata_path(model_path)
    if not p.exists():return {}
    try:return json.loads(p.read_text(encoding="utf-8"))
    except Exception:return {}

def write_render_metadata(model_path,output,size):
    METADATA_DIR.mkdir(parents=True,exist_ok=True)
    p=Path(model_path);st=p.stat()
    data={"model":str(p),"output":str(output),"render_version":RENDER_VERSION,"rendered":time.time(),"size":size,
          "source_size":st.st_size,"source_mtime_ns":st.st_mtime_ns}
    metadata_path(model_path).write_text(json.dumps(data,indent=2),encoding="utf-8")
    return data

def remove_cached_thumbnail(model_path,size=512):
    p=thumbnail_path(model_path,size);existed=p.exists()
    p.unlink(missing_ok=True);metadata_path(model_path).unlink(missing_ok=True)
    return existed

def _script(obj_path,out_path,size):
    s=WORK_DIR/f"render_{thumbnail_key(obj_path)}_{int(time.time()*1000)}.py"
    code=f'''import bpy, math
from mathutils import Vector
from pathlib import Path
obj=Path({str(obj_path)!r}); out=Path({str(out_path)!r}); size={int(size)}
bpy.ops.object.select_all(action="SELECT"); bpy.ops.object.delete(use_global=False)
try: bpy.ops.wm.obj_import(filepath=str(obj), forward_axis="Y", up_axis="Z")
except Exception: bpy.ops.import_scene.obj(filepath=str(obj), axis_forward="Y", axis_up="Z")
meshes=[o for o in bpy.context.scene.objects if o.type=="MESH"]
# LOD0 only; collision and distant LODs are intentionally hidden.
visible=[o for o in meshes if o.name.split(".")[0]=="LOD0"]
if not visible: visible=[o for o in meshes if not o.name.upper().startswith("COL") and not o.name.upper().startswith("LOD")]
if not visible: visible=[o for o in meshes if not o.name.upper().startswith("COL")]
for o in meshes: o.hide_render=o not in visible
# Preserve alpha from texture-driven materials (notably buggy window layers).
for mat in bpy.data.materials:
    if not mat or not mat.use_nodes: continue
    bsdf=mat.node_tree.nodes.get("Principled BSDF")
    texnodes=[n for n in mat.node_tree.nodes if n.type=="TEX_IMAGE" and n.image]
    if bsdf and texnodes:
        if any(n.label=="There _4 Window Opacity" for n in texnodes): continue
        tex=texnodes[0]
        try:
            mat.node_tree.links.new(tex.outputs["Alpha"],bsdf.inputs["Alpha"])
            mat.surface_render_method="DITHERED"
        except Exception: pass
pts=[]
for o in visible:
    pts.extend([o.matrix_world @ Vector(c) for c in o.bound_box])
if not pts: raise RuntimeError("No renderable model geometry")
mn=Vector((min(p.x for p in pts),min(p.y for p in pts),min(p.z for p in pts)))
mx=Vector((max(p.x for p in pts),max(p.y for p in pts),max(p.z for p in pts)))
center=(mn+mx)/2; extent=max((mx-mn).x,(mx-mn).y,(mx-mn).z,0.01)
scene=bpy.context.scene
scene.render.engine="BLENDER_EEVEE_NEXT"
scene.render.resolution_x=size; scene.render.resolution_y=size; scene.render.resolution_percentage=100
scene.render.film_transparent=False
scene.render.image_settings.file_format="PNG"; scene.render.filepath=str(out)
scene.render.resolution_percentage=100
world=scene.world or bpy.data.worlds.new("World"); scene.world=world
world.use_nodes=True; world.node_tree.nodes["Background"].inputs["Color"].default_value=(0.42,0.42,0.42,1); world.node_tree.nodes["Background"].inputs["Strength"].default_value=0.8
cam_data=bpy.data.cameras.new("InspectorCamera"); cam=bpy.data.objects.new("InspectorCamera",cam_data); scene.collection.objects.link(cam); scene.camera=cam
cam.data.type="ORTHO"; cam.data.ortho_scale=extent*1.32
direction=Vector((1.35,-1.65,1.05)).normalized(); cam.location=center+direction*extent*3
def track(obj,target):
    obj.rotation_euler=((target-obj.location).to_track_quat("-Z","Y")).to_euler()
track(cam,center)
for name,energy,size_l,loc in [("Key",1100,extent*2.0,(2.2,-2.4,3.0)),("Fill",650,extent*2.5,(-2.4,-0.5,1.6)),("Rim",900,extent*1.8,(0.4,2.6,2.8))]:
    d=bpy.data.lights.new(name,"AREA"); d.energy=energy; d.shape="DISK"; d.size=max(size_l,1.0)
    o=bpy.data.objects.new(name,d); scene.collection.objects.link(o); o.location=center+Vector(loc)*extent; track(o,center)
out.parent.mkdir(parents=True,exist_ok=True)
bpy.ops.render.render(write_still=True)
'''
    s.write_text(code,encoding="utf-8"); return s

def render_model_thumbnail(model_path,linked_textures=None,size=512,force=False):
    model_path=Path(model_path)
    CACHE_DIR.mkdir(parents=True,exist_ok=True); WORK_DIR.mkdir(parents=True,exist_ok=True)
    out=thumbnail_path(model_path,size)
    if out.exists() and not force:return {"success":True,"cached":True,"output":str(out)}
    blender=find_blender()
    if not blender:return {"success":False,"message":"Blender not detected"}
    decoded=decode_model(model_path)
    obj=WORK_DIR/f"{thumbnail_key(model_path)}.obj"
    export_obj(decoded,obj,linked_textures or [],include_collision=False)
    script=_script(obj,out,size)
    proc=subprocess.run([blender,"--background","--factory-startup","--python",str(script)],capture_output=True,text=True,timeout=180)
    log=(proc.stdout or "")+"\
"+(proc.stderr or "")
    success=proc.returncode==0 and out.exists()
    if success:
        write_render_metadata(model_path,out,size);clear_thumbnail_failure(model_path)
    else:log_thumbnail_failure(model_path,log[-4000:] or "Render failed",proc.returncode)
    return {"success":success,"cached":False,"output":str(out),"returncode":proc.returncode,"log":log[-8000:]}

def render_model_variant(model_path,texture_paths,size=512,force=False):
    model_path=Path(model_path);texture_paths=[str(x) for x in texture_paths if x]
    VARIANT_CACHE_DIR.mkdir(parents=True,exist_ok=True);WORK_DIR.mkdir(parents=True,exist_ok=True)
    out=variant_thumbnail_path(model_path,texture_paths,size)
    if out.exists() and not force:return {"success":True,"cached":True,"output":str(out)}
    blender=find_blender()
    if not blender:return {"success":False,"message":"Blender not detected"}
    decoded=decode_model(model_path);key=variant_key(model_path,texture_paths,size);obj=WORK_DIR/f"variant_{key}.obj"
    export_obj(decoded,obj,texture_paths,include_collision=False);script=_script(obj,out,size)
    # Blender's OBJ/MTL importer does not reliably retain a separate map_d.
    # Build the known There buggy window shader explicitly: _3=RGB, _4=opacity.
    import re
    slots={}
    for tp in texture_paths:
        m=re.match(r"^\d+_([1-9]\d*)\.",Path(tp).name,re.IGNORECASE)
        if m:slots[int(m.group(1))]=str(Path(tp).resolve())
    if 3 in slots and 4 in slots:
        txt=script.read_text(encoding="utf-8")
        color_ready=str(blender_texture_path(slots[3],WORK_DIR/"decoded_textures").resolve())
        alpha_ready=str(blender_texture_path(slots[4],WORK_DIR/"decoded_textures").resolve())
        window_mat=next((m for m in decoded.materials if (m.map_mask & 0x03)==0x03),None)
        if window_mat is None and len(decoded.materials)>=2: window_mat=decoded.materials[1]
        window_name=re.sub(r"[^A-Za-z0-9_.-]+","_",window_mat.name or "").strip("_") if window_mat else ""
        setup=f"WINDOW_MATERIAL={window_name!r}\nWINDOW_COLOR={color_ready!r}\nWINDOW_ALPHA={alpha_ready!r}\n"
        block='''# Explicit There buggy window shader.
try:
    color_img=bpy.data.images.load(WINDOW_COLOR,check_existing=True)
    alpha_img=bpy.data.images.load(WINDOW_ALPHA,check_existing=True)
    # Target the actual There material carrying COLOR + OPACITY semantics.
    target=None
    for mat in bpy.data.materials:
        if not mat or not mat.use_nodes: continue
        if WINDOW_MATERIAL and (mat.name==WINDOW_MATERIAL or mat.name.startswith(WINDOW_MATERIAL+".")):
            target=mat; break
    if target:
        nodes=target.node_tree.nodes; links=target.node_tree.links; bsdf=nodes.get("Principled BSDF")
        if bsdf:
            color_node=None
            if bsdf.inputs["Base Color"].is_linked:
                candidate=bsdf.inputs["Base Color"].links[0].from_node
                if candidate and candidate.type=="TEX_IMAGE": color_node=candidate
            if color_node is None: color_node=next((n for n in nodes if n.type=="TEX_IMAGE" and n.image),None)
            alpha_node=nodes.new("ShaderNodeTexImage"); alpha_node.image=alpha_img; alpha_node.label="There _4 Window Opacity"; alpha_node.image.colorspace_settings.name="Non-Color"
            if color_node:
                color_node.image=color_img
                color_node.label="There _3 Window Color"
            else:
                color_node=nodes.new("ShaderNodeTexImage"); color_node.image=color_img; color_node.label="There _3 Window Color"
            links.new(color_node.outputs["Color"],bsdf.inputs["Base Color"])
            if color_node.inputs["Vector"].is_linked:
                src=color_node.inputs["Vector"].links[0].from_socket; links.new(src,alpha_node.inputs["Vector"])
            links.new(alpha_node.outputs["Color"],bsdf.inputs["Alpha"])
            bsdf.inputs["Roughness"].default_value=0.22
            target.surface_render_method="DITHERED"
except Exception as e:
    print("There window shader warning:",e)
'''
        marker="# Preserve alpha from texture-driven materials (notably buggy window layers)."
        txt=setup+txt.replace(marker,block+"\n"+marker)
        script.write_text(txt,encoding="utf-8")
    proc=subprocess.run([blender,"--background","--factory-startup","--python",str(script)],capture_output=True,text=True,timeout=180)
    log=(proc.stdout or "")+"\
"+(proc.stderr or "");success=proc.returncode==0 and out.exists()
    return {"success":success,"cached":False,"output":str(out),"returncode":proc.returncode,"log":log[-8000:]}

def log_thumbnail_failure(model_path,message,returncode=None):
    FAILURE_LOG.parent.mkdir(parents=True,exist_ok=True)
    record={"time":time.time(),"model":str(model_path),"returncode":returncode,"message":str(message)[-4000:]}
    with FAILURE_LOG.open("a",encoding="utf-8") as fh:fh.write(json.dumps(record,ensure_ascii=False)+"\
")

def thumbnail_failures(unresolved_only=True):
    if not FAILURE_LOG.exists():return []
    latest={}
    try:
        for line in FAILURE_LOG.read_text(encoding="utf-8").splitlines():
            if not line.strip():continue
            r=json.loads(line); latest[r.get("model","")]=r
    except Exception:return []
    rows=sorted(latest.values(),key=lambda x:x.get("time",0),reverse=True)
    if unresolved_only:rows=[r for r in rows if r.get("model") and not cached_thumbnail(r["model"])]
    return rows

def clear_thumbnail_failure(model_path):
    if not FAILURE_LOG.exists():return
    keep=[]
    for line in FAILURE_LOG.read_text(encoding="utf-8").splitlines():
        try:
            r=json.loads(line)
            if r.get("model")!=str(model_path):keep.append(line)
        except Exception:keep.append(line)
    FAILURE_LOG.write_text(("\
".join(keep)+"\
") if keep else "",encoding="utf-8")

def thumbnail_failure_count():
    return len(thumbnail_failures(True))

def purge_thumbnail_cache():
    removed=0
    if CACHE_DIR.exists():
        for p in CACHE_DIR.glob("*.png"):
            p.unlink(missing_ok=True);removed+=1
    return removed
