# There Inspector v2.6.0

## New: Texture Comparison Rules / False Positive Reduction

v2.6 pushes texture comparison farther by adding role-aware down-weighting.

New texture rules:

- `bg\` buggy convention:
  - `_1` = buggy body texture, high-value comparison
  - `_2` / `_3` = buggy window layers, heavily down-weighted
- flat / single-color / mask-like textures are down-weighted
- named textures are treated as likely official/template/base resources
- numeric PID `_1` textures are treated as high-value primary artwork
- numeric PID secondary slots are treated as moderate value

## Why this matters

Common base textures and window layers were likely creating false positives. v2.6 keeps tracking them, but they should no longer drive suspicion scores or texture evidence results.

## Recommended after updating

Run:

```text
Research / Analysis > Rebuild Texture Evidence Pairs
Research / Analysis > Rebuild Asset Intelligence
```

Or run:

```text
Research / Analysis > Full Analysis - Incremental Scans
```
