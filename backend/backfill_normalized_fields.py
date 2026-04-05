from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from app.config import get_settings
from app.services.gemini import GeminiService
from app.services.supabase import get_supabase_service


def _coverage_bucket(row: dict) -> str:
    status = str(row.get("coverage_status") or "").strip().lower()
    if status == "not_covered":
        return "not_covered"
    if row.get("step_therapy"):
        return "step_therapy"
    if row.get("prior_authorization"):
        return "pa_required"
    if status == "restricted":
        return "restricted"
    return "covered"


def main() -> int:
    settings = get_settings()
    gemini = GeminiService(settings)
    supabase = get_supabase_service(settings)

    docs = supabase.list_documents()
    doc_by_id = {doc.get("id"): doc for doc in docs if doc.get("id")}
    rows = supabase._request(
        "GET",
        "/rest/v1/drug_coverages",
        params={"select": "*", "limit": "1000", "order": "created_at.asc"},
    ).json()

    print("Backfilling normalized fields for {0} drug coverage row(s)...".format(len(rows)))
    updated = 0

    for row in rows:
        document = doc_by_id.get(row.get("document_id")) or {}
        brand_names = row.get("brand_names") or []
        generic_name = row.get("generic_name") or gemini._canonicalize_name(row.get("drug_name") or "")
        family_name = row.get("family_name") or generic_name
        product_name = row.get("product_name") or (brand_names[0] if brand_names else row.get("drug_name"))
        policy_name = row.get("policy_name") or document.get("title") or document.get("file_name")
        payload = {
            "generic_name": generic_name or None,
            "family_name": family_name or None,
            "product_name": product_name or None,
            "product_key": row.get("product_key") or (gemini._canonicalize_name(brand_names[0], preserve_brand=True) if brand_names else None),
            "policy_name": policy_name,
            "document_type": row.get("document_type") or document.get("document_type"),
            "coverage_bucket": row.get("coverage_bucket") or _coverage_bucket(row),
            "source_pages": row.get("source_pages") or [],
            "source_section": row.get("source_section") or "general",
            "evidence_snippet": row.get("evidence_snippet"),
        }
        supabase._request(
            "PATCH",
            "/rest/v1/drug_coverages",
            headers={"Prefer": "return=minimal"},
            params={"id": "eq.{0}".format(row["id"])},
            json=payload,
        )
        updated += 1

    print("Backfill complete. Updated {0} row(s).".format(updated))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
