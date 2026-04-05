"""
Preload PDFs directly via service calls — no HTTP, no timeouts.
Usage:
    py -3.11 preload.py "D:\path\to\file1.pdf" "D:\path\to\file2.pdf" ...

For versioning simulation (same payer, triggers auto-diff):
    py -3.11 preload.py "D:\path\to\bcbs_v1.pdf" "D:\path\to\bcbs_v2.pdf"
"""
from __future__ import annotations

import sys
import os
from datetime import date

# Make sure we can import from app/
sys.path.insert(0, os.path.dirname(__file__))

from app.config import get_settings
from app.schemas.document import DocumentCreate
from app.schemas.drug_coverage import DrugCoverageCreate
from app.services.gemini import GeminiService
from app.services.policy_pipeline import parse_policy_path, run_policy_extraction
from app.services.supabase import get_supabase_service


def process_pdf(path: str, settings, gemini: GeminiService, supabase) -> None:
    filename = os.path.basename(path)
    print(f"\n{'='*60}")
    print(f"Processing: {filename}")
    print(f"{'='*60}")

    # 1. Extract text
    print("Extracting text from PDF...")
    document = parse_policy_path(path)
    total_pages = len(document.pages)
    print(f"  Extracted {total_pages}/{total_pages} pages")
    print(f"  Extracted {len(document.raw_text):,} chars")
    print(f"  Document type: {document.document_type}")
    print(f"  Title: {document.title}")

    # 2. Detect payer
    print("Extracting structured metadata and coverage...")
    bundle = run_policy_extraction(document, gemini)
    payer = bundle.payer
    print(f"  Payer: {payer}")
    if bundle.plan_name:
        print(f"  Plan name: {bundle.plan_name}")
    if bundle.state:
        print(f"  State: {bundle.state}")
    print(f"  Policy number: {bundle.policy_number}")
    if bundle.primary_drug:
        print(f"  Primary drug/family: {bundle.primary_drug}")

    # 3. Find or create plan
    plan_id = supabase.find_or_create_plan_for_payer(
        payer,
        plan_name=bundle.plan_name,
        state=bundle.state,
    )
    print(f"  Plan ID: {plan_id}")

    # 4. Chunk text
    print("Chunking text...")
    print(f"  {len(bundle.chunks)} chunks created")

    # 5. Extract coverages
    print(f"Extracting drug coverages ({len(bundle.tagged_chunks)} RAG chunks stored, filtered extraction complete)...")
    extracted = bundle.extracted
    if extracted.payer:
        payer = extracted.payer
        plan_id = supabase.find_or_create_plan_for_payer(
            payer,
            plan_name=bundle.plan_name,
            state=bundle.state,
        )
    policy_number = bundle.policy_number
    print(f"  Payer confirmed: {payer}")
    print(f"  Policy number: {policy_number}")
    print(f"  Drugs found: {len(extracted.coverages)}")

    # 6. Check for previous version
    prev_doc = None
    version = 1
    prev_doc_id = None
    try:
        prev_doc = supabase.find_previous_version(payer, policy_number)
        if prev_doc:
            version = (prev_doc.get("version") or 1) + 1
            prev_doc_id = prev_doc.get("id")
            print(f"  Previous version found! This will be v{version}")
    except Exception as e:
        print(f"  Version check error (non-fatal): {e}")

    # 7. Create document
    fingerprint = supabase._make_fingerprint(payer, policy_number)
    doc = supabase.create_document(DocumentCreate(
        plan_id=plan_id,
        file_name=filename,
        title=bundle.document.title,
        document_type=document.document_type,
        raw_text=document.raw_text,
        status="processed",
        payer=payer,
        policy_number=policy_number,
        effective_date=bundle.effective_date,
        last_reviewed_date=bundle.last_reviewed_date,
        version=version,
        previous_version_id=prev_doc_id,
        policy_fingerprint=fingerprint,
    ))
    document_id = doc["id"]
    print(f"  Document ID: {document_id} (v{version})")

    # 8. Tag chunks + save (no extra Gemini calls)
    print("Saving RAG chunks...")
    tagged = bundle.tagged_chunks
    supabase.save_chunks(document_id, tagged)
    print(f"  Saved {len(tagged)} chunks")

    # 9. Save drug coverages
    if extracted.coverages:
        payloads = [
            DrugCoverageCreate(
                plan_id=plan_id,
                document_id=document_id,
                drug_name=item.drug_name,
                generic_name=item.generic_name,
                family_name=item.family_name,
                product_name=item.product_name,
                product_key=item.product_key,
                policy_name=item.policy_name or bundle.plan_name or bundle.document.title,
                document_type=item.document_type or bundle.document.document_type,
                brand_names=item.brand_names,
                hcpcs_code=item.hcpcs_code,
                drug_tier=item.drug_tier,
                covered_indications=item.covered_indications,
                prior_authorization=item.prior_authorization,
                prior_auth_criteria=item.prior_auth_criteria,
                quantity_limit=item.quantity_limit,
                quantity_limit_detail=item.quantity_limit_detail,
                step_therapy=item.step_therapy,
                step_therapy_requirements=item.step_therapy_requirements,
                site_of_care=item.site_of_care,
                prescriber_requirements=item.prescriber_requirements,
                coverage_status=item.coverage_status,
                coverage_bucket=item.coverage_bucket,
                source_pages=item.source_pages,
                source_section=item.source_section,
                evidence_snippet=item.evidence_snippet,
                notes=item.notes,
                confidence_score=item.confidence_score,
                payer=payer,
                policy_number=policy_number,
                effective_date=bundle.effective_date,
                last_reviewed_date=bundle.last_reviewed_date,
            )
            for item in extracted.coverages
        ]
        supabase.replace_drug_coverages_for_document(document_id, payloads)
        print(f"  Saved {len(payloads)} drug coverage records")

    # 10. Auto-diff if previous version
    if prev_doc and prev_doc_id:
        print("Running auto-diff against previous version...")
        try:
            old_cov = supabase.list_drug_coverages(document_id=prev_doc_id)
            new_cov = supabase.list_drug_coverages(document_id=document_id)
            if old_cov and new_cov:
                diff = gemini.diff_policy_documents(
                    rows_a=old_cov, rows_b=new_cov,
                    label_a=f"{payer} v{version-1}",
                    label_b=f"{payer} v{version}",
                )
                change_rows = [
                    {
                        "payer": payer,
                        "drug_name": c.get("drug_name"),
                        "document_id_old": prev_doc_id,
                        "document_id_new": document_id,
                        "change_type": _map_change_type(c.get("change_type", "modified"), c.get("impact", "neutral")),
                        "field_changed": c.get("field"),
                        "old_value": str(c.get("old_value") or "")[:500],
                        "new_value": str(c.get("new_value") or "")[:500],
                        "impact": c.get("impact", "neutral"),
                        "summary": f"{c.get('field', '')}: {c.get('change_type', '')}",
                        "net_impact": diff.get("net_impact"),
                        "patient_impact_summary": diff.get("patient_impact_summary"),
                        "change_date": date.today().isoformat(),
                    }
                    for c in (diff.get("changes") or [])
                ]
                supabase.save_policy_changes(change_rows)
                print(f"  {len(change_rows)} policy changes saved")
        except Exception as e:
            print(f"  Diff error (non-fatal): {e}")

    print(f"\nDone: {filename} | {payer} v{version} | {len(extracted.coverages)} drugs | {len(tagged)} chunks")


def _map_change_type(change_type: str, impact: str) -> str:
    if change_type == "removed":
        return "coverage_removed"
    if change_type == "added":
        return "new_coverage" if impact == "less_restrictive" else "restriction_added"
    if impact == "more_restrictive":
        return "restriction_added"
    if impact == "less_restrictive":
        return "coverage_expanded"
    return "criteria_updated"


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: py -3.11 preload.py <file1.pdf> [file2.pdf] ...")
        sys.exit(1)

    settings = get_settings()
    gemini = GeminiService(settings)
    supabase = get_supabase_service(settings)

    files = sys.argv[1:]
    print(f"Processing {len(files)} file(s)...")

    for path in files:
        if not os.path.exists(path):
            print(f"File not found: {path} — skipping")
            continue
        try:
            process_pdf(path, settings, gemini, supabase)
        except Exception as e:
            print(f"FAILED: {path} — {e}")

    print("\n\nAll done.")
