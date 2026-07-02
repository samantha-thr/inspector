# There Inspector v1.5.0

There Inspector is a local There.com `.model` scanner, SQLite indexer, explorer, and comparison tool.

## v1.5.0 changes

- Adds comprehensive binary fingerprints for model comparison.
- Adds prefix, middle, and suffix block hashes.
- Adds byte entropy, printable-byte ratio, and zero-byte ratio.
- Adds sample string extraction from the full file.
- Improves internal similarity scoring for models that may share geometry but differ by texture or metadata.
- Expands model detail and folder detail with forensic metrics.
- Prepares comparison data for later geometry and public web/IP matching.

## Important

To populate the new fingerprint columns, run **Scan Client Resources** once after updating.
Unchanged files from older versions may be skipped; if needed, delete `database/inspector.db` and rescan to rebuild all fingerprints.
