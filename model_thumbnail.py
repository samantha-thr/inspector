from __future__ import annotations
import hashlib, json, math, subprocess, time
from pathlib import Path
from config import PROJECT_DIR
from model_converter import find_blender
from there_model_decoder import decode_model, export_obj

CACHE_DIR=PROJECT_DIR/"cache"/"model_thumbnails"
WORK_DIR=PROJECT_DIR/"cache"/"thumbnail_work"

def thumbnail_key(model_path):
    p=Path(model_path)
    st=p.stat()
    return hashlib.sha256(f"{p.resolve()}|{st.st_size}|{st.st_mtime_ns}".encode()).hexdigest()[:24]

def thumbnail_path(model_path,size=512):
    return CACHE_DIR/f"{thumbnail_key(model_path)}_{size}.png"

def cached_thumbnail(model_path,size=512):
    p=thumbnail_path(model_path,size)
    return p if p.exists() else None

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
    log=(proc.stdout or "")+"\n"+(proc.stderr or "")
    return {"success":proc.returncode==0 and out.exists(),"cached":False,"output":str(out),"returncode":proc.returncode,"log":log[-8000:]}

def purge_thumbnail_cache():
    removed=0
    if CACHE_DIR.exists():
        for p in CACHE_DIR.glob("*.png"):
            p.unlink(missing_ok=True);removed+=1
    return removed
