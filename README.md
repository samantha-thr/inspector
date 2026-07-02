# There Inspector v1.2.0

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

or use `there_inspector.bat`.

## Default scan path

```text
C:\Makena\There\ThereClient\Resources
```

## v1.2.0 changes

- Default "Scan Client Resources" option
- Better Rich progress display
- SQLite batching for faster scans
- Incremental scanning using file size and modified time
- Filename classification
- Search database
- Statistics screen
