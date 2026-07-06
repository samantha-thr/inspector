# There Inspector v2.7.2 Hotfix

Fixes the Full Analysis crash during Texture Evidence Pairs:

```text
AttributeError: 'Database' object has no attribute 'texture_evidence_candidate_groups'
```

Changed:
- `analysis_engine.py`
- `config.py`

This hotfix adds fallback helpers inside `analysis_engine.py`, so texture evidence can build even if the Database helper methods are missing.
