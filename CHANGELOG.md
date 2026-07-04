# Changelog

## v2.3.0

- Fixed CSV export PermissionError by using timestamped report filenames.
- Moved evidence exports into `reports/evidence/`.
- Reworked texture evidence builder so it can generate evidence directly from texture fingerprints.
- Texture evidence no longer requires texture families to be rebuilt first.

## v2.2.1

- Fixed missing `texture_evidence_pairs` table on upgraded v2 databases.
