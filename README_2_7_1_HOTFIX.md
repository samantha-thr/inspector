# There Inspector v2.7.1 Hotfix

Fixes the Analysis Run History crash:

```text
AttributeError: 'Database' object has no attribute 'recent_analysis_runs'
```

Changed:
- `database.py`
- `config.py`

Apply these files over v2.7.0, then reopen **Analyze Resources > Analysis Run History**.
