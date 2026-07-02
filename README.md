# There Inspector v1.2.3

There Inspector is a local There.com `.model` scanner and SQLite indexer.

## Install

```bat
install.bat
```

or:

```bat
python -m pip install -r requirements.txt
```

## Run

```bat
python there_inspector.py
```

or use:

```bat
there_inspector.bat
```

## Default scan path

```text
C:\Makena\There\ThereClient\Resources
```

## v1.2.3 changes

- Adds richer scan progress with hashed, skipped, error, and speed counters.
- Adds basic SOM header inspection.
- Adds SOM version counts to scan summaries and statistics.
- Adds relative paths to the database.
- Adds larger database statistics, including total size, average size, largest model, and smallest model.
- Adds CSV export for search results.
- Adds JSON summary export.
- Keeps automatic database migration for older v1.0-v1.2 databases.
