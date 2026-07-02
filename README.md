# There Inspector v1.6.1

## v1.6.1 changes

- Adds `.dds` as the primary texture scan extension.
- Adds DDS header parsing.
- Stores DDS width, height, mipmaps, FourCC, format, alpha, cubemap/volume flags, and estimated VRAM.
- Adds DDS format searching and format summaries.
- Improves texture similarity scoring using DDS format and mipmap information.
- Keeps Pillow pixel analysis when Pillow can decode the texture.
- Falls back to DDS header-only analysis when Pillow cannot decode a compressed DDS.

## Recommended after updating

Run:

```text
Scan Manager > Full Texture Rescan
```

This populates the new DDS metadata columns.
