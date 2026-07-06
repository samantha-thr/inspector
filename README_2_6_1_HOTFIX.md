# There Inspector v2.6.1 Hotfix

Fixes the texture evidence crash:

```text
AttributeError: 'Database' object has no attribute 'texture_evidence_candidate_groups'
```

## Changed

- Adds `Database.texture_evidence_candidate_groups()`
- Adds `Database.texture_members_for_group()`
- Bumps version to `2.6.1`

After replacing files, rerun:

```text
Full Analysis - Incremental Scans
```

or:

```text
Rebuild Texture Evidence Pairs
```
