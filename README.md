# There Inspector v2.3.0

## Fixes

- Evidence CSV exports now use timestamped filenames so Windows/Excel file locks do not cause permission errors.
- Exports are written to:
  - `reports/evidence/model_evidence_pairs_YYYYMMDD_HHMMSS.csv`
  - `reports/evidence/texture_evidence_pairs_YYYYMMDD_HHMMSS.csv`

## Texture evidence improvement

Texture evidence no longer depends on rebuilding texture families first. It directly builds candidate groups from:

- exact SHA256
- perceptual average hash
- color histogram hash

This should prevent the `0 texture evidence` issue when texture families exist but the evidence table has not been populated.

## Recommended after updating

1. Research / Analysis > Rebuild Texture Evidence Pairs
2. Research / Analysis > Export Texture Evidence CSV
