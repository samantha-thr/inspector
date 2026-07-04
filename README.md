# There Inspector v2.4.0

## New

Adds one-click **Full Analysis** options in both **Scan Manager** and **Research / Analysis**.

Options:

- **Full Analysis - Incremental Scans**
- **Full Analysis - Full Rescan**

Pipeline order:

1. Model scan
2. Texture scan
3. Rebuild model ↔ texture links
4. Rebuild model families
5. Rebuild texture families
6. Rebuild model evidence pairs
7. Rebuild texture evidence pairs
8. Export model evidence CSV
9. Export texture evidence CSV
10. Export full analysis summary

Reports:

```text
reports/evidence/model_evidence_pairs_YYYYMMDD_HHMMSS.csv
reports/evidence/texture_evidence_pairs_YYYYMMDD_HHMMSS.csv
reports/analysis/full_analysis_summary_YYYYMMDD_HHMMSS.txt
```
