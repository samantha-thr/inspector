# There Inspector v1.6.0

## v1.6.0 changes

- Adds Scan Manager.
- Adds separate Incremental Model Scan and Full Model Rescan.
- Adds separate Incremental Texture Scan and Full Texture Rescan.
- Adds texture database table.
- Adds texture hashing and optional pixel analysis using Pillow.
- Adds texture duplicate browser.
- Adds similar texture search using dimensions, average color, alpha, and perceptual-style average hash.
- Updates statistics to include texture totals.

## Install

```bat
install.bat
```

This installs:

- rich
- Pillow

## Important

Run **Scan Manager > Full Model Rescan** once after v1.5 if you need all model fingerprint columns rebuilt.
Run **Scan Manager > Full Texture Rescan** once to populate the texture database.
