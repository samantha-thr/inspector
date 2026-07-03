# There Inspector v2.2.1

## Fix

Fixes startup crash:

```text
sqlite3.OperationalError: no such table: texture_evidence_pairs
```

This version forces the `texture_evidence_pairs` table and indexes to be created during database initialization.

## After updating

Start There Inspector normally. Then run:

```text
Research / Analysis > Rebuild Texture Evidence Pairs
```
