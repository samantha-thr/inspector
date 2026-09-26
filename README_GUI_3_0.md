# There Inspector 3.0 GUI

The `gui-3.0` branch is the native PySide6 desktop evolution of There Inspector. It keeps the existing forensic database and analysis engine while replacing the command-line-first workflow with a visual investigation workspace.

## Run

```bat
pip install -r requirements.txt
python there_inspector_gui.py
```

## Current 3.0-dev5 capabilities

- live dashboard with model, texture, evidence, review and last-scan information
- scalable model/texture browsing with SQL paging, sorting and 250–5000 row page sizes
- visual texture previews, including DDS through Pillow when supported
- model Asset Profiles with linked-texture visual galleries
- Overview, Relationships, Evidence and Review tabs
- review status, priority, tags and investigation notes
- dedicated prioritized Review Queue
- interactive Evidence workspace with model/texture mode, minimum-score filter and row limits
- side-by-side texture comparison with previews and forensic metadata
- native There model inspection/conversion to OBJ, BLEND, GLB and GLTF
- knowledge-rule editor
- one-click Incremental and Full analysis pipelines plus advanced individual jobs
- progress percentage, elapsed time, ETA and processing rate
- persistent resource/Blender settings
- compact diagnostic JSON export for sharing real-world database statistics without committing the SQLite database
- local database and generated outputs excluded from Git

## Visual roadmap

The next major visual layer is automatic LOD0 model thumbnail rendering through Blender. Once cached, those renders can be reused in Models, Asset Profiles, Evidence, Compare and Dashboard without repeatedly launching Blender.
