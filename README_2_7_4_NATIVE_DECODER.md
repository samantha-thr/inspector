# There Inspector v2.7.4 - Native There Model Decoder

v2.7.4 adds the first native `.model` geometry decoder.

## Native decoding

Supported now:

- SOM signature / version 10
- material records
- node names / hierarchy / transforms
- collision geometry
- LODs and LOD distances
- mesh positions
- normals
- UV0 / UV1
- vertex colors
- tangents / bitangents when present
- material assignments
- triangle indices

## Conversion

- `.model -> .obj` works directly in Python.
- `.model -> .blend` uses the native decoder, then Blender background mode.
- `.model -> .glb/.gltf` uses the native decoder, then Blender background mode.
- linked DDS textures from There Inspector are assigned to generated materials on a best-effort basis.

## Current scope

The decoder targets SOM version 10, which is the format written by the current There Blender exporter and matches the supplied test models.

If an older/different SOM variant is encountered, the converter reports it as unsupported rather than guessing.
