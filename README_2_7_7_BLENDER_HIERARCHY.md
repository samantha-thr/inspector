# There Inspector v2.7.7 - Blender Hierarchy Cleanup

The converted COL/LOD meshes were already parented to `master`, but OBJ import
left them linked to the import/default collection as well. Blender could therefore
show the same objects at the collection root and again beneath `master`.

v2.7.7 moves every converted mesh exclusively into a dedicated `ThereModel`
collection and keeps `COL`, `LOD0`, `LOD1`, etc. directly parented to `master`.

Expected Outliner:

```text
ThereModel
└─ master
   ├─ COL
   ├─ LOD0
   ├─ LOD1
   └─ LOD2
```

The actual number of LOD children follows the source model.
