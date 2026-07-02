# There Inspector v2.0.0

v2 is a clean rebuild of the scanner, database, relationship engine, and UI.

## Why v2?

The v1.x branch proved the ideas, but the database and UI drifted as features were added.
v2 starts fresh with a stable schema and consistent analysis pipeline.

## Install

```bat
install.bat
```

## Run

```bat
python there_inspector.py
```

## Recommended first run

1. Scan Manager > Full Model Rescan
2. Scan Manager > Full Texture Rescan
3. Research / Analysis > Rebuild Model ↔ Texture Links
4. Research / Analysis > Rebuild Model Families

## Important

v2 uses a new database file:

```text
database/inspector_v2.db
```

Your v1 database is not modified.
