# There Inspector v1.7.2

## v1.7.2 changes

- Fixes SQLite transaction error when rebuilding model-texture links.
- Also applies the same transaction-safety fix to model family rebuilding.
- Keeps the v1.7.1 fast PID-based texture linking logic.

## Recommended after updating

Run:

```text
Research / Relationship Analysis > Rebuild Model ↔ Texture Candidate Links
```
