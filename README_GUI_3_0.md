# There Inspector 3.0 GUI Preview

This branch starts the native desktop GUI using PySide6 / Qt Widgets while preserving the existing 2.x CLI and database.

## Run

pip install -r requirements.txt
python there_inspector_gui.py

The current foundation includes a modern dark shell, persistent navigation, live database dashboard, searchable model and texture browsers, and model/texture evidence views. Compare, Convert, Knowledge, Scan & Analysis, and Settings are present as the next implementation workspaces.

The existing CLI remains available with:

python there_inspector.py

No database reset is required.
