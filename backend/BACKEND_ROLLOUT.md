**Backend Rollout**
1. Apply the new SQL migration:
   [004_policy_metadata_and_reports.sql](/Users/vaishnavibhalodi/Downloads/rxpulse/backend/db/004_policy_metadata_and_reports.sql)

2. Reingest the three sample policies so the new normalized fields populate:
```bash
cd /Users/vaishnavibhalodi/Downloads/rxpulse
backend/venv/bin/python backend/reingest_policies.py
```

Optional faster path for existing rows already in Supabase:
```bash
backend/venv/bin/python backend/backfill_normalized_fields.py
```
This backfills normalized columns on existing `drug_coverages`, but full reingest is still better for extraction-quality improvements.

3. Verify the richer backend responses:
```bash
backend/venv/bin/python - <<'PY'
import sys
sys.path.insert(0, 'backend')
from app.main import app
print('routes', len(app.routes))
PY
```

4. Spot-check these API endpoints after reingest:
- `GET /api/v1/stats`
- `GET /api/v1/search/policy?drug=rituximab`
- `GET /api/v1/compare/plans?drug=botox`
- `GET /api/v1/coverage-matrix`
- `GET /api/v1/reports/drug?drug=rituximab`
- `POST /api/v1/qa/ask`

**Important Notes**
- The migration is required before the new fields can be stored.
- Existing already-ingested rows will not magically gain the new normalized fields; they need reingest.
- Reingest uses the current versioning behavior, so it may create newer `documents` versions instead of replacing history.
