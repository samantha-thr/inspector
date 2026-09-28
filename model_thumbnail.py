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
VARIANT_RENDER_VERSION=7
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

def render_model_variant(model_path,texture_paths,size=512,force=False,cancel_event=None):
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
    window_color_slot,window_alpha_slot=(3,4) if 4 in slots else ((2,3) if 2 in slots and 3 in slots else (None,None))
    if window_color_slot and window_alpha_slot:
        txt=script.read_text(encoding="utf-8")
        color_ready=str(blender_texture_path(slots[window_color_slot],WORK_DIR/"decoded_textures").resolve())
        alpha_ready=str(blender_texture_path(slots[window_alpha_slot],WORK_DIR/"decoded_textures").resolve())
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
    proc=subprocess.Popen([blender,"--background","--factory-startup","--python",str(script)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    started=time.monotonic()
    while proc.poll() is None:
        if cancel_event is not None and cancel_event.is_set():
            proc.terminate()
            try: proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill();proc.wait()
            return {"success":False,"cancelled":True,"cached":False,"output":str(out),"returncode":proc.returncode,"log":"Cancelled by user"}
        if time.monotonic()-started>180:
            proc.kill();proc.wait();raise subprocess.TimeoutExpired(proc.args,180)
        time.sleep(.10)
    stdout,stderr=proc.communicate()
    log=(stdout or "")+"\n"+(stderr or "");success=proc.returncode==0 and out.exists()
    return {"success":success,"cached":False,"output":str(out),"returncode":proc.returncode,"log":log[-8000:]}


class PersistentVariantWorker:
    """Long-lived Blender process for high-throughput vehicle variant renders."""
    def __init__(self,model_path,seed_texture_paths,size=512,cancel_event=None,worker_id=0):
        import re
        self.model_path=Path(model_path);self.size=size;self.cancel_event=cancel_event;self.proc=None
        self.decoded=decode_model(self.model_path)
        token=hashlib.sha256(f"{self.model_path.resolve()}|{worker_id}|{time.time_ns()}".encode()).hexdigest()[:12]
        self.obj=WORK_DIR/f"persistent_{token}.obj";self.script=WORK_DIR/f"persistent_{token}.py"
        WORK_DIR.mkdir(parents=True,exist_ok=True);VARIANT_CACHE_DIR.mkdir(parents=True,exist_ok=True)
        export_obj(self.decoded,self.obj,seed_texture_paths,include_collision=False)
        body_mat=next((m for m in self.decoded.materials if (m.map_mask&1) and not (m.map_mask&6)),None)
        if body_mat is None and self.decoded.materials: body_mat=self.decoded.materials[0]
        window_mat=next((m for m in self.decoded.materials if (m.map_mask&3)==3),None)
        if window_mat is None and len(self.decoded.materials)>=2: window_mat=self.decoded.materials[1]
        safe=lambda m: re.sub(r"[^A-Za-z0-9_.-]+","_",m.name or "").strip("_") if m else ""
        base=_script(self.obj,VARIANT_CACHE_DIR/f"_persistent_probe_{token}.png",size).read_text(encoding="utf-8")
        base=base.rsplit('bpy.ops.render.render(write_still=True)',1)[0]
        loop=f'''
import sys,json,traceback
BODY_MATERIAL={safe(body_mat)!r}
WINDOW_MATERIAL={safe(window_mat)!r}
def find_mat(name):
    for m in bpy.data.materials:
        if m and m.use_nodes and name and (m.name==name or m.name.startswith(name+".")): return m
    return None
body_mat=find_mat(BODY_MATERIAL); window_mat=find_mat(WINDOW_MATERIAL)
def image_node_for_base(mat):
    if not mat:return None,None
    nodes=mat.node_tree.nodes;bsdf=nodes.get("Principled BSDF")
    if not bsdf:return None,None
    node=None
    if bsdf.inputs["Base Color"].is_linked:
        n=bsdf.inputs["Base Color"].links[0].from_node
        if n and n.type=="TEX_IMAGE":node=n
    if node is None:
        node=next((n for n in nodes if n.type=="TEX_IMAGE"),None)
    if node is None:node=nodes.new("ShaderNodeTexImage")
    return node,bsdf
body_node,body_bsdf=image_node_for_base(body_mat)
window_color_node,window_bsdf=image_node_for_base(window_mat)
window_alpha_node=None
if window_mat and window_bsdf:
    nodes=window_mat.node_tree.nodes;links=window_mat.node_tree.links
    window_alpha_node=next((n for n in nodes if n.type=="TEX_IMAGE" and n.label=="There Window Opacity"),None)
    if window_alpha_node is None:
        window_alpha_node=nodes.new("ShaderNodeTexImage");window_alpha_node.label="There Window Opacity"
    if window_color_node and window_color_node.inputs["Vector"].is_linked:
        links.new(window_color_node.inputs["Vector"].links[0].from_socket,window_alpha_node.inputs["Vector"])
    links.new(window_alpha_node.outputs["Color"],window_bsdf.inputs["Alpha"])
    window_bsdf.inputs["Roughness"].default_value=0.22
    window_mat.surface_render_method="DITHERED"
print("THERE_READY",flush=True)
for line in sys.stdin:
    try:
        job=json.loads(line)
        if job.get("cmd")=="quit":break
        body=bpy.data.images.load(job["body"],check_existing=True);body_node.image=body
        if window_color_node and job.get("window_color"):
            window_color_node.image=bpy.data.images.load(job["window_color"],check_existing=True)
        if window_alpha_node and job.get("window_alpha"):
            ai=bpy.data.images.load(job["window_alpha"],check_existing=True);ai.colorspace_settings.name="Non-Color";window_alpha_node.image=ai
        scene.render.filepath=job["output"];Path(job["output"]).parent.mkdir(parents=True,exist_ok=True)
        bpy.ops.render.render(write_still=True)
        print("THERE_RESULT|"+str(job["pid"])+"|OK|"+job["output"],flush=True)
    except Exception as e:
        print("THERE_RESULT|"+str(job.get("pid","?"))+"|FAIL|"+str(e).replace("|","/"),flush=True)
'''
        self.script.write_text(base+loop,encoding="utf-8")
        blender=find_blender()
        if not blender: raise RuntimeError("Blender not detected")
        self.proc=subprocess.Popen([blender,"--background","--factory-startup","--python",str(self.script)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,bufsize=1)
        deadline=time.monotonic()+180
        while time.monotonic()<deadline:
            if self.cancel_event is not None and self.cancel_event.is_set(): self.close(force=True);raise RuntimeError("Cancelled")
            line=self.proc.stdout.readline()
            if not line and self.proc.poll() is not None: raise RuntimeError("Persistent Blender worker exited during startup")
            if "THERE_READY" in line:return
        self.close(force=True);raise RuntimeError("Persistent Blender worker startup timed out")
    def render(self,pid,texture_paths,force=False):
        import re
        out=variant_thumbnail_path(self.model_path,texture_paths,self.size)
        if out.exists() and not force:return {"success":True,"cached":True,"output":str(out)}
        if self.cancel_event is not None and self.cancel_event.is_set():return {"success":False,"cancelled":True}
        slots={}
        for tp in texture_paths:
            m=re.match(r"^\d+_([1-9]\d*)\.",Path(tp).name,re.I)
            if m:slots[int(m.group(1))]=str(Path(tp).resolve())
        wc,wa=(3,4) if 4 in slots else ((2,3) if 2 in slots and 3 in slots else (None,None))
        if 1 not in slots:return {"success":False,"message":"Variant has no _1 body texture","output":str(out)}
        decoded_dir=WORK_DIR/"decoded_textures"
        job={"pid":str(pid),"body":str(blender_texture_path(slots[1],decoded_dir).resolve()),"window_color":str(blender_texture_path(slots[wc],decoded_dir).resolve()) if wc else None,"window_alpha":str(blender_texture_path(slots[wa],decoded_dir).resolve()) if wa else None,"output":str(out.resolve())}
        try:
            self.proc.stdin.write(json.dumps(job)+"\n");self.proc.stdin.flush()
            while True:
                if self.cancel_event is not None and self.cancel_event.is_set():self.close(force=True);return {"success":False,"cancelled":True,"output":str(out)}
                line=self.proc.stdout.readline()
                if not line and self.proc.poll() is not None:return {"success":False,"message":"Persistent Blender worker exited","returncode":self.proc.returncode,"output":str(out)}
                if line.startswith("THERE_RESULT|"):
                    parts=line.rstrip().split("|",3)
                    ok=len(parts)>2 and parts[2]=="OK" and out.exists()
                    return {"success":ok,"cached":False,"output":str(out),"returncode":0 if ok else 1,"message":None if ok else (parts[3] if len(parts)>3 else "Render failed"),"log":line.rstrip()}
        except Exception as e:return {"success":False,"message":str(e),"output":str(out)}
    def close(self,force=False):
        if not self.proc:return
        try:
            if self.proc.poll() is None and not force and self.proc.stdin:
                self.proc.stdin.write('{"cmd":"quit"}\n');self.proc.stdin.flush();self.proc.wait(timeout=5)
        except Exception:pass
        if self.proc.poll() is None:
            self.proc.terminate()
            try:self.proc.wait(timeout=3)
            except Exception:self.proc.kill()
        self.proc=None

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
