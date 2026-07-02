# There Inspector v1.7.1

## v1.7.1 changes

- Replaces brute-force model-texture linking with There-aware naming rules.
- Numeric product models now link directly to `PID_*.dds` textures.
- Named/official models are still checked for matching external DDS textures.
- Named/official models with no texture matches are marked as `likely_baked_texture`.
- Adds model texture status values:
  - `linked_external_dds`
  - `possible_external_dds`
  - `likely_baked_texture`
  - `no_texture_found`
  - `needs_som_parse`
- Adds status counts to relationship stats.

## Recommended after updating

Run:

```text
Research / Relationship Analysis > Rebuild Model ↔ Texture Candidate Links
```

This version should be dramatically faster than v1.7.0.
