# Changelog

## v1.7.2

- Fixed `sqlite3.OperationalError: cannot start a transaction within a transaction`.
- Committed table-clearing operations before starting batched inserts.
- Applied transaction safety to both texture-link and family rebuilds.

## v1.7.1

- Reworked model-texture relationship building.
- Added fast PID-based texture linking.
- Still checks official/named models for matching external DDS textures.
- Marks likely baked-texture models when no DDS candidate exists.
- Adds model texture relationship status table.
