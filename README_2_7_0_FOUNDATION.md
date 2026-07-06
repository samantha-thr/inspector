# There Inspector v2.7.0 Foundation

This is the first platform/refactor release.

## New workflow shell

The main menu is now organized around investigation workflows:

- Analyze Resources
- Investigate Assets
- Browse Library
- Knowledge Base
- Reports
- Settings
- Legacy Tools

Existing scanning, evidence, intelligence, search, and export features are still available.

## New platform pieces

- `pipeline.py`
  - Central full-analysis pipeline definition.

- `knowledge.py`
  - JSON-backed knowledge pack loader.

- `knowledge/default_knowledge.json`
  - Initial There.com resource knowledge:
    - `bg` = Buggy
    - `fu` = Furniture
    - `fl` = Flowers
    - `prop` = Props
    - `bg *_1` = body texture
    - `bg *_2 / *_3` = window layer, down-weighted

## New database tables

- `knowledge_rules`
- `asset_reviews`
- `asset_tags`
- `asset_notes`
- `analysis_runs`

These are foundation tables for the 2.7 investigation platform.

## Recommended test

1. Start There Inspector.
2. Open **Knowledge Base > View Folder Knowledge**.
3. Open **Analyze Resources > Quick Analyze / Incremental Full Analysis**.
4. Open **Investigate Assets > Top Suspicious Assets**.
5. Open **Reports > Export All Core Reports**.
