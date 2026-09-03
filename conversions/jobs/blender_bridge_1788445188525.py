import bpy
from pathlib import Path
source = Path('conversions\\output\\2cool billboard.decoder.obj')
out = Path('conversions\\output\\2cool billboard.blend')
fmt = 'blend'
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
try:
    bpy.ops.wm.obj_import(filepath=str(source))
except Exception:
    bpy.ops.import_scene.obj(filepath=str(source))
out.parent.mkdir(parents=True, exist_ok=True)
if fmt == 'blend':
    bpy.ops.wm.save_as_mainfile(filepath=str(out))
elif fmt == 'glb':
    bpy.ops.export_scene.gltf(filepath=str(out), export_format='GLB')
elif fmt == 'gltf':
    bpy.ops.export_scene.gltf(filepath=str(out), export_format='GLTF_SEPARATE')
else:
    raise RuntimeError('Unsupported Blender output format: ' + fmt)