# There Inspector v1.7.0

## v1.7.0 changes

- Adds Research / Relationship Analysis menu.
- Adds inferred model-to-texture candidate links.
- Adds model family clustering based on exact hashes and high-value binary fingerprints.
- Shows candidate texture links inside Model Explorer.
- Shows candidate model links inside Similar Textures.
- Adds relationship/family counts to status and statistics.

## Recommended workflow

After updating:

1. Run **Scan Manager > Full Model Rescan** if v1.5 fingerprints are not populated.
2. Run **Scan Manager > Full Texture Rescan** if DDS metadata is not populated.
3. Run **Research / Relationship Analysis > Rebuild Model ↔ Texture Candidate Links**.
4. Run **Research / Relationship Analysis > Rebuild Model Families**.

## Important

Model ↔ texture links are currently heuristic candidates only. They use folder/name
proximity until SOM texture references are decoded.
