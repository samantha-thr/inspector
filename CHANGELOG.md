# Changelog

## v2.6.0

- Added texture role rules.
- Added bg buggy texture convention:
  - `_1` body texture kept high-value.
  - `_2` and `_3` window layers heavily down-weighted.
- Added flat/single-color/mask-like texture down-weighting.
- Added named official/template texture down-weighting.
- Updated texture evidence scoring to reduce common/base false positives.
- Texture evidence report now includes downweighted skipped counts.

## v2.5.0

- Added Asset Intelligence Layer.
- Added reuse score, suspicion score, and fingerprint score.
