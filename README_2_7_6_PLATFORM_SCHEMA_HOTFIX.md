# There Inspector v2.7.6 - Platform Schema Hotfix

Fixes:

```text
sqlite3.OperationalError: no such table: asset_reviews
```

An early 2.7 hotfix replaced `database.py` with a pre-2.7 copy and unintentionally
removed several platform tables and helper methods.

v2.7.6 restores the complete 2.7 database layer.

Restored tables:
- `knowledge_rules`
- `asset_reviews`
- `asset_tags`
- `asset_notes`
- `analysis_runs`

Restored helpers:
- texture evidence candidate groups
- review status
- tags
- notes
- knowledge rules
- analysis run history

The migration is automatic and preserves existing model, texture, evidence,
intelligence, and scan data.
