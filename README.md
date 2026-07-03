# There Inspector v2.1.0

v2.1 adds the first real Evidence Engine for finding possible reused, recolored, re-exported, or improperly credited assets.

## New in v2.1

- Texture intelligence:
  - alpha coverage
  - edge density
  - brightness
  - saturation
  - grayscale detection
  - probable normal-map detection
  - color histogram fingerprint

- Texture families:
  - exact SHA256
  - perceptual average hash
  - color histogram family

- Evidence pairs:
  - binary score
  - texture score
  - string score
  - overall evidence score
  - evidence reason text

- Evidence CSV export:
  - reports/evidence_pairs.csv

## Recommended after updating from v2.0

1. Scan Manager > Full Texture Rescan
2. Research / Analysis > Rebuild Model ↔ Texture Links
3. Research / Analysis > Rebuild Model Families
4. Research / Analysis > Rebuild Texture Families
5. Research / Analysis > Rebuild Evidence Pairs

Evidence scores are investigative leads only. They are not ownership or infringement conclusions.
