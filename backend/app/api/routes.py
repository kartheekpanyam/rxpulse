from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from app.config import get_settings
from app.schemas.document import DocumentCreate, DocumentRead
from app.schemas.drug_coverage import (
    CompareResponse,
    CoverageMatrixResponse,
    DiffChange,
    DiffResponse,
    DrugReportResponse,
    DrugCoverageCreate,
    DrugCoverageRead,
    PlanCoverageEntry,
    PolicyCoverageRead,
    PolicySearchResponse,
    QARequest,
    QAResponse,
    _compact_list,
    _compact_text,
)
from app.schemas.plan import PlanCreate, PlanRead
from app.services.gemini import GeminiService
from app.services.policy_pipeline import parse_policy_bytes, run_policy_extraction
from app.services.supabase import get_supabase_service
from app.services.upload_jobs import get_upload_job_manager


router = APIRouter()


# ---------------------------------------------------------------------------
# Upload result schema
# ---------------------------------------------------------------------------

class UploadResult(BaseModel):
    document_id: str
    file_name: str
    payer: str
    policy_number: Optional[str] = None
    drugs_extracted: int = 0
    chunks_stored: int = 0
    version: int = 1
    is_new_version: bool = False
    changes_detected: int = 0
    message: str = ""


class UploadJobStatus(BaseModel):
    job_id: str
    status: str
    file_name: str
    message: str
    stage: Optional[str] = None
    document_id: Optional[str] = None
    result: Optional[UploadResult] = None
    error: Optional[str] = None
    created_at: str
    updated_at: str


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@router.get("/health")
def health_check() -> Dict[str, object]:
    settings = get_settings()
    gemini = GeminiService(settings)
    supabase = get_supabase_service(settings)

    return {
        "status": "ok",
        "service": settings.app_name,
        "environment": settings.app_env,
        "integrations": {
            "gemini": gemini.status(),
            "supabase": supabase.status(),
        },
    }


@router.get("/")
def read_root() -> Dict[str, str]:
    settings = get_settings()
    return {
        "message": "{0} is running.".format(settings.app_name),
    }


@router.get("/health/supabase")
def supabase_health_check() -> Dict[str, object]:
    supabase = get_supabase_service()
    return supabase.check_connection()


# ---------------------------------------------------------------------------
# Plans
# ---------------------------------------------------------------------------

@router.get("/plans", response_model=List[PlanRead])
def list_plans() -> List[PlanRead]:
    supabase = get_supabase_service()
    try:
        rows = supabase.list_plans()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return [PlanRead(**row) for row in rows]


@router.post("/plans", response_model=PlanRead, status_code=201)
def create_plan(payload: PlanCreate) -> PlanRead:
    supabase = get_supabase_service()
    try:
        row = supabase.create_plan(payload)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return PlanRead(**row)


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------

@router.get("/documents", response_model=List[DocumentRead])
def list_documents(plan_id: Optional[str] = None) -> List[DocumentRead]:
    supabase = get_supabase_service()
    try:
        rows = supabase.list_documents(plan_id=plan_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return [DocumentRead(**row) for row in rows]


# ---------------------------------------------------------------------------
# Simplified upload: full pipeline (Task 4 + 5)
# ---------------------------------------------------------------------------

@router.post("/upload", response_model=UploadJobStatus, status_code=202)
async def upload_policy_pdf(file: UploadFile = File(...)) -> UploadJobStatus:
    """Queue the full pipeline in the background and return a job id immediately."""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Please upload a PDF file.")

    file_bytes = await file.read()
    manager = get_upload_job_manager()
    job = manager.create_job(file.filename)
    manager.submit(job["job_id"], _process_uploaded_policy_bytes, file.filename, file_bytes)
    return UploadJobStatus(**job)


@router.get("/upload/jobs/{job_id}", response_model=UploadJobStatus)
def get_upload_job(job_id: str) -> UploadJobStatus:
    manager = get_upload_job_manager()
    job = manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Upload job not found.")
    return UploadJobStatus(**job)


@router.post("/upload/sync", response_model=UploadResult, status_code=201)
async def upload_policy_pdf_sync(file: UploadFile = File(...)) -> UploadResult:
    """Synchronous fallback for operator use on small PDFs."""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Please upload a PDF file.")

    file_bytes = await file.read()
    return _process_uploaded_policy_bytes(file.filename, file_bytes)


def _process_uploaded_policy_bytes(file_name: str, file_bytes: bytes, on_progress: Any = None) -> UploadResult:
    """Full pipeline upload helper used by both sync and background ingestion."""

    def _report(stage: str, message: str) -> None:
        if on_progress:
            on_progress(stage, message)

    settings = get_settings()
    gemini = GeminiService(settings)
    supabase = get_supabase_service(settings)

    _report("parsing", "Extracting text from PDF...")
    try:
        document = parse_policy_bytes(file_bytes, source_name=file_name)
    except Exception as exc:
        raise ValueError("PDF extraction failed: {0}".format(exc))

    _report("extracting", "Extracting drug coverages with AI...")
    try:
        bundle = run_policy_extraction(document, gemini)
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Coverage extraction failed: {0}".format(exc))

    _report("storing", "Saving plan and document records...")
    payer = bundle.payer
    try:
        plan_id = supabase.find_or_create_plan_for_payer(
            payer,
            plan_name=bundle.plan_name,
            state=bundle.state,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Plan creation failed: {0}".format(exc))
    policy_number = bundle.policy_number

    # 6. Check for previous version by fingerprint
    prev_doc = None
    version = 1
    prev_doc_id: Optional[str] = None
    try:
        prev_doc = supabase.find_previous_version(payer, policy_number)
        if prev_doc:
            version = (prev_doc.get("version") or 1) + 1
            prev_doc_id = prev_doc.get("id")
    except Exception:
        pass

    # 7. Create document record
    fingerprint = supabase._make_fingerprint(payer, policy_number)
    doc_payload = DocumentCreate(
        plan_id=plan_id,
        file_name=file_name,
        title=bundle.document.title,
        document_type=bundle.document.document_type,
        raw_text=bundle.document.raw_text,
        status="processed",
        payer=payer,
        policy_number=policy_number,
        effective_date=bundle.effective_date,
        last_reviewed_date=bundle.last_reviewed_date,
        version=version,
        previous_version_id=prev_doc_id,
        policy_fingerprint=fingerprint,
    )
    try:
        doc_row = supabase.create_document(doc_payload)
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Document creation failed: {0}".format(exc))

    document_id = doc_row["id"]

    _report("chunking", "Generating embeddings and saving RAG chunks...")
    # 8. Tag chunks with payer/drug metadata, generate embeddings, and save
    tagged_chunks = bundle.tagged_chunks
    try:
        # Generate embeddings for each chunk
        texts = [c.get("content", "")[:2048] for c in tagged_chunks]
        embeddings = gemini.embed_texts_batch(texts)
        for chunk, emb in zip(tagged_chunks, embeddings):
            if emb:
                chunk["embedding"] = emb
        supabase.save_chunks(document_id, tagged_chunks)
    except Exception:
        pass  # Non-fatal — RAG degrades gracefully

    # 8b. Verify extraction with second pass (multi-model verification)
    _report("extracting", "Verifying extraction accuracy...")
    try:
        verified = gemini.verify_extraction(bundle.document, bundle.extracted)
    except Exception:
        verified = bundle.extracted

    # 9. Save structured drug coverages to drug_coverages table
    drugs_saved = 0
    if verified.coverages:
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
            for item in verified.coverages
        ]
        try:
            supabase.replace_drug_coverages_for_document(document_id, payloads)
            drugs_saved = len(payloads)
        except Exception:
            pass

    _report("diffing", "Checking for policy changes...")
    # 10. Auto-diff if previous version exists → save policy_changes
    changes_detected = 0
    if prev_doc and prev_doc_id:
        try:
            old_coverages = supabase.list_drug_coverages(document_id=prev_doc_id)
            new_coverages = supabase.list_drug_coverages(document_id=document_id)
            if old_coverages and new_coverages:
                diff_result = gemini.diff_policy_documents(
                    rows_a=old_coverages,
                    rows_b=new_coverages,
                    label_a="{0} v{1}".format(payer, version - 1),
                    label_b="{0} v{1}".format(payer, version),
                )
                change_rows = [
                    {
                        "payer": payer,
                        "drug_name": c.get("drug_name"),
                        "document_id_old": prev_doc_id,
                        "document_id_new": document_id,
                        "policy_number": policy_number,
                        "change_type": _map_diff_change_type(
                            c.get("change_type", "modified"), c.get("impact", "neutral")
                        ),
                        "field_changed": c.get("field"),
                        "old_value": str(c.get("old_value") or "")[:500],
                        "new_value": str(c.get("new_value") or "")[:500],
                        "impact": c.get("impact", "neutral"),
                        "summary": "{0}: {1}".format(c.get("field", ""), c.get("change_type", "")),
                        "net_impact": diff_result.get("net_impact"),
                        "patient_impact_summary": diff_result.get("patient_impact_summary"),
                        "change_date": date.today().isoformat(),
                    }
                    for c in (diff_result.get("changes") or [])
                ]
                supabase.save_policy_changes(change_rows)
                changes_detected = len(change_rows)
        except Exception:
            pass  # Non-fatal

    _report("finalizing", "Wrapping up...")
    msg = "Processed {0}: {1} drug(s), {2} chunks, {3} change(s) detected.".format(
        file_name, drugs_saved, len(tagged_chunks), changes_detected
    )
    if version > 1:
        msg = "Version {0} — ".format(version) + msg

    return UploadResult(
        document_id=document_id,
        file_name=file_name,
        payer=payer,
        policy_number=policy_number,
        drugs_extracted=drugs_saved,
        chunks_stored=len(tagged_chunks),
        version=version,
        is_new_version=prev_doc is not None,
        changes_detected=changes_detected,
        message=msg,
    )


def _map_diff_change_type(change_type: str, impact: str) -> str:
    if change_type == "removed":
        return "coverage_removed"
    if change_type == "added":
        return "new_coverage" if impact == "less_restrictive" else "restriction_added"
    # modified
    if impact == "more_restrictive":
        return "restriction_added"
    if impact == "less_restrictive":
        return "coverage_expanded"
    return "criteria_updated"


# Legacy upload kept for backward compat (requires plan_id)
@router.post("/documents/upload", response_model=DocumentRead, status_code=201)
async def upload_document(
    plan_id: str = Form(...),
    document_type: str = Form("formulary"),
    source_url: Optional[str] = Form(None),
    file: UploadFile = File(...),
) -> DocumentRead:
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Please upload a PDF file.")

    try:
        file_bytes = await file.read()
        document = parse_policy_bytes(file_bytes, source_name=file.filename)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="PDF extraction failed: {0}".format(exc))

    payload = DocumentCreate(
        plan_id=plan_id,
        file_name=file.filename,
        title=document.title,
        document_type=document_type,
        source_url=source_url,
        raw_text=document.raw_text,
        status="processed",
    )

    supabase = get_supabase_service()
    try:
        row = supabase.create_document(payload)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return DocumentRead(**row)

# ---------------------------------------------------------------------------
# Dashboard stats (Task 6)
# ---------------------------------------------------------------------------

@router.get("/stats")
def get_stats(payer: Optional[str] = None) -> Dict:
    """Return aggregate dashboard stats, optionally filtered by payer."""
    supabase = get_supabase_service()
    try:
        return supabase.get_dashboard_stats(payer=payer or None)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# Policy changes feed (Task 10)
# ---------------------------------------------------------------------------

@router.get("/policy-changes")
def list_policy_changes(
    payer: Optional[str] = None,
    drug: Optional[str] = None,
    limit: int = 50,
) -> List[Dict]:
    """Return persistent policy change records from the policy_changes table."""
    supabase = get_supabase_service()
    try:
        return supabase.list_policy_changes(payer=payer, drug=drug, limit=limit)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# Drug coverages
# ---------------------------------------------------------------------------

@router.get("/drugs/list")
def list_drugs() -> List[Dict]:
    """Return unique drug names with payer counts for the search dropdown."""
    supabase = get_supabase_service()
    try:
        rows = supabase._request(
            "GET", "/rest/v1/drug_coverages",
            params={"select": "drug_name,payer", "limit": "500"},
        ).json()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    drug_payers: Dict[str, dict] = {}
    for r in rows:
        dn = (r.get("drug_name") or "").strip()
        py = (r.get("payer") or "").strip()
        if dn:
            drug_payers.setdefault(dn.lower(), {"name": dn, "payers": set()})
            drug_payers[dn.lower()]["name"] = dn
            if py:
                drug_payers[dn.lower()]["payers"].add(py)

    result = sorted(
        [{"drug_name": v["name"], "payer_count": len(v["payers"])} for v in drug_payers.values()],
        key=lambda x: (-x["payer_count"], x["drug_name"]),
    )
    return result


@router.get("/drug-coverages", response_model=List[DrugCoverageRead])
def list_drug_coverages(
    plan_id: Optional[str] = None,
    document_id: Optional[str] = None,
    limit: Optional[int] = None,
) -> List[DrugCoverageRead]:
    supabase = get_supabase_service()
    try:
        rows = supabase.list_drug_coverages(plan_id=plan_id, document_id=document_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    if limit is not None:
        rows = rows[:limit]
    return [DrugCoverageRead(**row) for row in rows]


@router.get("/search/drug", response_model=List[DrugCoverageRead])
def search_drug(
    drug: str,
    payer: Optional[str] = None,
    limit: int = 50,
) -> List[DrugCoverageRead]:
    if not drug.strip():
        raise HTTPException(status_code=400, detail="'drug' query parameter is required.")
    supabase = get_supabase_service()
    try:
        rows = supabase.search_drug_coverages(drug=drug.strip(), payer=payer, limit=limit)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return [DrugCoverageRead(**row) for row in rows]


@router.get("/search/policy", response_model=PolicySearchResponse)
def search_policy(
    drug: str,
    payer: Optional[str] = None,
    limit: int = 50,
) -> PolicySearchResponse:
    """Frontend-facing search — returns grouped PolicySearchResponse with nested shapes."""
    if not drug.strip():
        raise HTTPException(status_code=400, detail="'drug' query parameter is required.")
    supabase = get_supabase_service()
    try:
        rows = supabase.search_drug_coverages(drug=drug.strip(), payer=payer, limit=limit)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    policies = [PolicyCoverageRead.from_flat(row) for row in rows]
    hcpcs_code = next((p.hcpcs_code for p in policies if p.hcpcs_code), None)
    generic_name = (
        next((row.get("generic_name") for row in rows if row.get("generic_name")), None)
        or (rows[0]["drug_name"] if rows else drug.strip())
    )
    policy_count = len({
        "|".join([
            str(row.get("payer") or ""),
            str(row.get("policy_name") or row.get("policy_number") or ""),
            str(row.get("product_key") or row.get("drug_name") or ""),
        ])
        for row in rows
    })

    return PolicySearchResponse(
        drug=drug.strip(),
        generic_name=generic_name,
        hcpcs_code=hcpcs_code,
        payer_policies_found=policy_count,
        policies=policies,
    )


@router.get("/compare/plans", response_model=CompareResponse)
def compare_plans(
    drug: str,
    payers: Optional[str] = None,
) -> CompareResponse:
    if not drug.strip():
        raise HTTPException(status_code=400, detail="'drug' query parameter is required.")

    payer_list = [p.strip() for p in payers.split(",")] if payers else []

    supabase = get_supabase_service()
    try:
        rows = supabase.compare_drug_across_payers(drug=drug.strip(), payers=payer_list or None)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    entries = [
        PlanCoverageEntry(
            payer=row.get("payer"),
            policy_name=row.get("policy_name"),
            policy_number=row.get("policy_number"),
            drug_name=row.get("drug_name", drug),
            generic_name=row.get("generic_name"),
            family_name=row.get("family_name"),
            product_name=row.get("product_name"),
            brand_names=row.get("brand_names") or [],
            hcpcs_code=row.get("hcpcs_code"),
            coverage_status=row.get("coverage_status"),
            coverage_bucket=row.get("coverage_bucket"),
            prior_authorization=row.get("prior_authorization", False),
            prior_auth_criteria=_compact_list(row.get("prior_auth_criteria") or [], limit=6),
            step_therapy=row.get("step_therapy", False),
            step_therapy_requirements=_compact_list(row.get("step_therapy_requirements") or [], limit=4),
            quantity_limit=row.get("quantity_limit", False),
            quantity_limit_detail=row.get("quantity_limit_detail"),
            covered_indications=_compact_list(row.get("covered_indications") or [], limit=5),
            site_of_care=row.get("site_of_care") or [],
            prescriber_requirements=row.get("prescriber_requirements"),
            effective_date=row.get("effective_date"),
            source_pages=row.get("source_pages") or [],
            evidence_snippet=_compact_text(row.get("evidence_snippet"), max_len=180),
            notes=_compact_text(row.get("notes"), max_len=320),
        )
        for row in rows
    ]

    payers_found = sorted({e.payer for e in entries if e.payer})

    return CompareResponse(
        drug=drug.strip(),
        payers_requested=payer_list,
        payers_found=payers_found,
        results=entries,
    )


@router.get("/coverage-matrix", response_model=CoverageMatrixResponse)
def coverage_matrix(
    drug: Optional[str] = None,
    payers: Optional[str] = None,
) -> CoverageMatrixResponse:
    payer_list = [p.strip() for p in payers.split(",")] if payers else []
    supabase = get_supabase_service()
    try:
        matrix = supabase.build_coverage_matrix(drug=drug, payers=payer_list or None)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return CoverageMatrixResponse(**matrix)


@router.get("/reports/drug", response_model=DrugReportResponse)
def drug_report(
    drug: str,
    payer: Optional[str] = None,
    limit: int = 20,
) -> DrugReportResponse:
    if not drug.strip():
        raise HTTPException(status_code=400, detail="'drug' query parameter is required.")

    supabase = get_supabase_service()
    settings = get_settings()
    gemini = GeminiService(settings)
    try:
        rows = supabase.search_drug_coverages(drug=drug.strip(), payer=payer, limit=limit)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    policies = [PolicyCoverageRead.from_flat(row) for row in rows]
    payers_found = sorted({p.payer for p in policies if p.payer})
    context_rows = rows[:12]
    if context_rows:
        question = "Generate a plain-English coverage report for {0}, comparing payers and highlighting coverage, prior authorization, step therapy, quantity limits, and notable restrictions.".format(drug.strip())
        try:
            report = gemini.ask_question(question, context_rows)
            summary = report.get("answer") or ""
        except Exception:
            summary = ""
    else:
        summary = "No policy data found for that drug."

    return DrugReportResponse(
        drug=drug.strip(),
        generated_summary=summary,
        policies_found=len(policies),
        payers_found=payers_found,
        supporting_policies=policies,
    )


# ---------------------------------------------------------------------------
# QA / AI Assistant (Task 7: RAG-based)
# ---------------------------------------------------------------------------

@router.post("/qa/ask", response_model=QAResponse)
def ask_question(payload: QARequest) -> QAResponse:
    if not payload.question.strip():
        raise HTTPException(status_code=400, detail="'question' is required.")
    supabase = get_supabase_service()
    settings = get_settings()
    gemini = GeminiService(settings)
    # Use vector search (with keyword fallback) for RAG chunks
    try:
        query_embedding = gemini.embed_text(payload.question)
        chunks = supabase.retrieve_chunks_for_question(
            payload.question, query_embedding=query_embedding
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    try:
        result = gemini.ask_question_rag(payload.question, chunks)
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Gemini Q&A failed: {0}".format(exc))
    return QAResponse(**result)


@router.get("/qa/diff", response_model=DiffResponse)
def diff_documents(
    document_id_a: str,
    document_id_b: str,
) -> DiffResponse:
    supabase = get_supabase_service()
    settings = get_settings()
    gemini = GeminiService(settings)

    try:
        rows_a = supabase.list_drug_coverages(document_id=document_id_a)
        rows_b = supabase.list_drug_coverages(document_id=document_id_b)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    if not rows_a:
        raise HTTPException(status_code=404, detail="No drug coverages found for document_id_a.")
    if not rows_b:
        raise HTTPException(status_code=404, detail="No drug coverages found for document_id_b.")

    payer_a = rows_a[0].get("payer") or document_id_a
    payer_b = rows_b[0].get("payer") or document_id_b

    try:
        result = gemini.diff_policy_documents(
            rows_a=rows_a,
            rows_b=rows_b,
            label_a=payer_a,
            label_b=payer_b,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Gemini diff failed: {0}".format(exc))

    changes = [
        DiffChange(
            drug_name=c.get("drug_name", "unknown"),
            field=c.get("field", "other"),
            change_type=c.get("change_type", "modified"),
            old_value=c.get("old_value"),
            new_value=c.get("new_value"),
            impact=c.get("impact", "neutral"),
        )
        for c in (result.get("changes") or [])
    ]

    return DiffResponse(
        document_id_a=document_id_a,
        document_id_b=document_id_b,
        payer_a=payer_a,
        payer_b=payer_b,
        summary=result.get("summary", ""),
        net_impact=result.get("net_impact", "unknown"),
        patient_impact_summary=result.get("patient_impact_summary", ""),
        changes=changes,
        drugs_compared=max(len(rows_a), len(rows_b)),
    )


# ---------------------------------------------------------------------------
# Manual extraction (kept for operator use)
# ---------------------------------------------------------------------------

@router.post(
    "/documents/{document_id}/extract-drug-coverages",
    response_model=List[DrugCoverageRead],
)
def extract_drug_coverages_from_document(document_id: str) -> List[DrugCoverageRead]:
    supabase = get_supabase_service()
    settings = get_settings()
    gemini = GeminiService(settings)

    try:
        document = supabase.get_document(document_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    if not document:
        raise HTTPException(status_code=404, detail="Document not found.")

    raw_text = (document.get("raw_text") or "").strip()
    if not raw_text:
        raise HTTPException(status_code=400, detail="Document does not contain extractable raw_text.")

    try:
        extracted = gemini.extract_drug_coverages(raw_text)
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Gemini extraction failed: {0}".format(exc))

    try:
        supabase.update_document_metadata(
            document_id,
            {
                "payer": extracted.payer,
                "policy_number": extracted.policy_number,
                "effective_date": extracted.effective_date,
                "last_reviewed_date": extracted.last_reviewed_date,
            },
        )
    except Exception:
        pass

    payloads = [
        DrugCoverageCreate(
            plan_id=document["plan_id"],
            document_id=document["id"],
            drug_name=item.drug_name,
            generic_name=item.generic_name,
            family_name=item.family_name,
            product_name=item.product_name,
            product_key=item.product_key,
            policy_name=item.policy_name or document.get("title"),
            document_type=item.document_type or document.get("document_type"),
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
            payer=extracted.payer,
            policy_number=extracted.policy_number,
            effective_date=extracted.effective_date,
            last_reviewed_date=extracted.last_reviewed_date,
        )
        for item in extracted.coverages
    ]

    try:
        rows = supabase.replace_drug_coverages_for_document(document_id, payloads)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    return [DrugCoverageRead(**row) for row in rows]
