# Changelog Addition

## v2.7.5

- Fixed duplicate-looking LOD objects by consolidating model components into one OBJ/Blender object per actual LOD.
- Fixed 90-degree face-down Blender conversions by explicitly importing decoded geometry as Y-forward / Z-up.
- Added `master` parent object to converted Blender scenes.
- Added LOD distance custom properties to `master` and LOD objects.
- Improved OBJ naming and conversion metadata.
