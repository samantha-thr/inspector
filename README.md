# There Inspector v1.4.0

There Inspector is a local There.com `.model` scanner, SQLite indexer, explorer, and comparison tool.

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

## v1.4.0 changes

- Adds direct model-to-model comparison.
- Adds internal similar model search.
- Adds folder-to-folder comparison.
- Adds same-size, first-256, and exact-hash indexes.
- Shows relative paths more consistently.
- Adds duplicate rate and unique hash counts to folder details.
- Prepares the internal comparison foundation for later public web/IP matching.

## Notes on public/IP comparison

v1.4.0 compares models against the local indexed resource cache only.
Later versions can add web-resource matching using filenames, hashes, visual thumbnails, and extracted geometry fingerprints.
