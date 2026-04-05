from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


def _unique_strings(values: List[str]) -> List[str]:
    cleaned = []
    seen = set()
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(text)
    return cleaned


def _compact_list(values: List[str], limit: int) -> List[str]:
    deduped = _unique_strings(values)
    if len(deduped) <= limit:
        return deduped
    remaining = len(deduped) - limit
    return deduped[:limit] + ["... and {0} more".format(remaining)]


def _compact_text(value: Optional[str], max_len: int = 280) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return None
    if len(text) <= max_len:
        return text
    return text[: max_len - 3].rstrip() + "..."


class DrugCoverageExtractedItem(BaseModel):
    drug_name: str
    generic_name: Optional[str] = None
    family_name: Optional[str] = None
    product_name: Optional[str] = None
    product_key: Optional[str] = None
    policy_name: Optional[str] = None
    document_type: Optional[str] = None
    brand_names: List[str] = Field(default_factory=list)
    hcpcs_code: Optional[str] = None
    drug_tier: Optional[str] = None
    covered_indications: List[str] = Field(default_factory=list)
    prior_authorization: bool = False
    prior_auth_criteria: List[str] = Field(default_factory=list)
    quantity_limit: bool = False
    quantity_limit_detail: Optional[str] = None
    step_therapy: bool = False
    step_therapy_requirements: List[str] = Field(default_factory=list)
    site_of_care: List[str] = Field(default_factory=list)
    prescriber_requirements: Optional[str] = None
    coverage_status: Optional[str] = None
    coverage_bucket: Optional[str] = None
    source_pages: List[int] = Field(default_factory=list)
    source_section: Optional[str] = None
    evidence_snippet: Optional[str] = None
    notes: Optional[str] = None
    confidence_score: float = Field(default=0.5, ge=0.0, le=1.0)


class DrugCoverageExtractionResult(BaseModel):
    coverages: List[DrugCoverageExtractedItem] = Field(default_factory=list)
    payer: Optional[str] = None
    policy_number: Optional[str] = None
    effective_date: Optional[str] = None
    last_reviewed_date: Optional[str] = None


class DrugCoverageCreate(BaseModel):
    plan_id: str
    document_id: Optional[str] = None
    drug_name: str
    generic_name: Optional[str] = None
    family_name: Optional[str] = None
    product_name: Optional[str] = None
    product_key: Optional[str] = None
    policy_name: Optional[str] = None
    document_type: Optional[str] = None
    brand_names: List[str] = Field(default_factory=list)
    hcpcs_code: Optional[str] = None
    drug_tier: Optional[str] = None
    covered_indications: List[str] = Field(default_factory=list)
    prior_authorization: bool = False
    prior_auth_criteria: List[str] = Field(default_factory=list)
    quantity_limit: bool = False
    quantity_limit_detail: Optional[str] = None
    step_therapy: bool = False
    step_therapy_requirements: List[str] = Field(default_factory=list)
    site_of_care: List[str] = Field(default_factory=list)
    prescriber_requirements: Optional[str] = None
    coverage_status: Optional[str] = None
    coverage_bucket: Optional[str] = None
    source_pages: List[int] = Field(default_factory=list)
    source_section: Optional[str] = None
    evidence_snippet: Optional[str] = None
    notes: Optional[str] = None
    confidence_score: float = Field(default=0.5, ge=0.0, le=1.0)
    payer: Optional[str] = None
    policy_number: Optional[str] = None
    effective_date: Optional[str] = None
    last_reviewed_date: Optional[str] = None


class DrugCoverageRead(DrugCoverageCreate):
    id: str
    created_at: str
    updated_at: str


class StepTherapyShape(BaseModel):
    required: bool = False
    details: str = ""


class SiteOfCareShape(BaseModel):
    allowed: List[str] = Field(default_factory=list)
    restricted: List[str] = Field(default_factory=list)
    preferred: str = ""


class PolicyCoverageRead(BaseModel):
    """Frontend-facing shape for a single payer's coverage of a drug.
    Transforms the flat DrugCoverageRead into the nested shape the UI expects.
    """
    policy_id: str
    payer: Optional[str] = None
    policy_name: Optional[str] = None
    policy_number: Optional[str] = None
    effective_date: Optional[str] = None
    last_updated: Optional[str] = None
    drug_name: str
    generic_name: Optional[str] = None
    family_name: Optional[str] = None
    product_name: Optional[str] = None
    brand_names: List[str] = Field(default_factory=list)
    hcpcs_code: Optional[str] = None
    coverage_status: Optional[str] = None
    coverage_bucket: Optional[str] = None
    covered_indications: List[str] = Field(default_factory=list)
    prior_auth_required: bool = False
    pa_criteria: List[str] = Field(default_factory=list)
    step_therapy: StepTherapyShape = Field(default_factory=StepTherapyShape)
    site_of_care: SiteOfCareShape = Field(default_factory=SiteOfCareShape)
    clinical_criteria: List[str] = Field(default_factory=list)
    prescriber_requirements: Optional[str] = None
    quantity_limit: bool = False
    quantity_limit_detail: Optional[str] = None
    reauthorization_interval: Optional[str] = None
    evidence_snippet: Optional[str] = None
    source_pages: List[int] = Field(default_factory=list)
    confidence_score: float = 0.5

    @classmethod
    def from_flat(cls, row: dict) -> "PolicyCoverageRead":
        """Build a PolicyCoverageRead from a raw DrugCoverageRead dict."""
        # Step therapy: bool + requirements list → nested shape
        step_required = bool(row.get("step_therapy", False))
        step_reqs = _compact_list(row.get("step_therapy_requirements") or [], limit=4)
        step_details = "; ".join(step_reqs) if step_reqs else ("Required" if step_required else "")

        # Site of care: flat list → structured shape
        # Values like "hospital", "office", "home" stored in DB
        soc_raw = row.get("site_of_care") or []
        allowed = [s for s in soc_raw if s in ("hospital", "office", "home")]
        # preferred = first entry (lowest cost preferred by payers)
        preferred = allowed[0] if allowed else ""

        # policy_name: derive from payer + drug if not stored
        payer = row.get("payer") or ""
        drug = row.get("drug_name", "")
        policy_name = row.get("policy_name") or ("{0} — {1}".format(payer, drug) if payer else drug)

        return cls(
            policy_id=row.get("id", ""),
            payer=payer or None,
            policy_name=policy_name,
            policy_number=row.get("policy_number"),
            effective_date=row.get("effective_date"),
            last_updated=row.get("last_reviewed_date") or row.get("updated_at"),
            drug_name=drug,
            generic_name=row.get("generic_name"),
            family_name=row.get("family_name"),
            product_name=row.get("product_name"),
            brand_names=row.get("brand_names") or [],
            hcpcs_code=row.get("hcpcs_code"),
            coverage_status=row.get("coverage_status"),
            coverage_bucket=row.get("coverage_bucket"),
            covered_indications=_compact_list(row.get("covered_indications") or [], limit=6),
            prior_auth_required=bool(row.get("prior_authorization", False)),
            pa_criteria=_compact_list(row.get("prior_auth_criteria") or [], limit=8),
            step_therapy=StepTherapyShape(required=step_required, details=step_details),
            site_of_care=SiteOfCareShape(allowed=allowed, restricted=[], preferred=preferred),
            clinical_criteria=_compact_list(row.get("prior_auth_criteria") or [], limit=5),
            prescriber_requirements=row.get("prescriber_requirements"),
            quantity_limit=bool(row.get("quantity_limit", False)),
            quantity_limit_detail=row.get("quantity_limit_detail"),
            evidence_snippet=_compact_text(row.get("evidence_snippet"), max_len=220),
            source_pages=(row.get("source_pages") or [])[:8],
            confidence_score=float(row.get("confidence_score") or 0.5),
        )


class PolicySearchResponse(BaseModel):
    """Grouped response for /search/policy — one object per drug search result."""
    drug: str
    generic_name: str
    hcpcs_code: Optional[str] = None
    payer_policies_found: int = 0
    policies: List[PolicyCoverageRead] = Field(default_factory=list)


class QARequest(BaseModel):
    question: str
    zip_code: Optional[str] = None


class QAResponse(BaseModel):
    answer: str
    sources: List[str] = Field(default_factory=list)
    drugs_found: List[str] = Field(default_factory=list)


class PlanCoverageEntry(BaseModel):
    payer: Optional[str] = None
    policy_name: Optional[str] = None
    policy_number: Optional[str] = None
    drug_name: str
    generic_name: Optional[str] = None
    family_name: Optional[str] = None
    product_name: Optional[str] = None
    brand_names: List[str] = Field(default_factory=list)
    hcpcs_code: Optional[str] = None
    coverage_status: Optional[str] = None
    coverage_bucket: Optional[str] = None
    prior_authorization: bool = False
    prior_auth_criteria: List[str] = Field(default_factory=list)
    step_therapy: bool = False
    step_therapy_requirements: List[str] = Field(default_factory=list)
    quantity_limit: bool = False
    quantity_limit_detail: Optional[str] = None
    covered_indications: List[str] = Field(default_factory=list)
    site_of_care: List[str] = Field(default_factory=list)
    prescriber_requirements: Optional[str] = None
    effective_date: Optional[str] = None
    source_pages: List[int] = Field(default_factory=list)
    evidence_snippet: Optional[str] = None
    notes: Optional[str] = None


class CompareResponse(BaseModel):
    drug: str
    payers_requested: List[str] = Field(default_factory=list)
    payers_found: List[str] = Field(default_factory=list)
    results: List[PlanCoverageEntry] = Field(default_factory=list)


class CoverageMatrixCell(BaseModel):
    payer: str
    policy_name: Optional[str] = None
    policy_number: Optional[str] = None
    coverage_status: Optional[str] = None
    coverage_bucket: Optional[str] = None
    prior_authorization: bool = False
    step_therapy: bool = False
    quantity_limit: bool = False
    notes: Optional[str] = None


class CoverageMatrixRow(BaseModel):
    drug_name: str
    generic_name: Optional[str] = None
    family_name: Optional[str] = None
    product_name: Optional[str] = None
    brand_names: List[str] = Field(default_factory=list)
    cells: List[CoverageMatrixCell] = Field(default_factory=list)


class CoverageMatrixResponse(BaseModel):
    payers: List[str] = Field(default_factory=list)
    rows: List[CoverageMatrixRow] = Field(default_factory=list)


class DrugReportResponse(BaseModel):
    drug: str
    generated_summary: str
    policies_found: int = 0
    payers_found: List[str] = Field(default_factory=list)
    supporting_policies: List[PolicyCoverageRead] = Field(default_factory=list)


class DiffChange(BaseModel):
    drug_name: str
    field: str
    change_type: str  # added | removed | modified
    old_value: Optional[str] = None
    new_value: Optional[str] = None
    impact: str  # more_restrictive | less_restrictive | neutral


class DiffResponse(BaseModel):
    document_id_a: str
    document_id_b: str
    payer_a: Optional[str] = None
    payer_b: Optional[str] = None
    summary: str
    net_impact: str  # more_restrictive | less_restrictive | mixed | unchanged
    patient_impact_summary: str
    changes: List[DiffChange] = Field(default_factory=list)
    drugs_compared: int = 0
