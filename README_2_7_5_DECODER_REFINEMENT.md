# There Inspector v2.7.5 - Decoder Refinement

This update addresses the first in-Blender conversion test.

## Fixed

### Too many apparent LODs
The decoder was correctly finding three LOD levels, but each material/node component
was being written as a separate OBJ object. Blender therefore showed names such as:

- `LOD0_LOD0_0`
- `LOD0_LOD0_1`
- `LOD1_LOD0_0`
- `LOD1_LOD0_1`

These were components, not extra LOD levels.

v2.7.5 now writes exactly one object per real LOD:

- `LOD0`
- `LOD1`
- `LOD2`

Material/component boundaries remain inside the LOD object through material assignments.

### 90-degree / face-down conversion
The decoded geometry is already in Blender Z-up coordinates. Blender's OBJ importer
was applying its normal OBJ axis conversion a second time, creating the visible
90-degree X rotation.

The Blender bridge now explicitly imports as:

- Forward: Y
- Up: Z

and builds a `master` parent with the actual LOD distance properties.

## Expected Blender hierarchy

```text
master
├─ COL
├─ LOD0
├─ LOD1
└─ LOD2
```

Models with a different real LOD count will naturally contain that number instead.
