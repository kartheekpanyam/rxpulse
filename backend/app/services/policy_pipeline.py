from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.schemas.drug_coverage import DrugCoverageExtractionResult
from app.services.gemini import GeminiService
from app.services.pdf_policy_parser import (
    PolicyDocument,
    build_rag_chunks,
    parse_pdf_bytes,
    parse_pdf_path,
)


@dataclass(frozen=True)
class PolicyExtractionBundle:
    document: PolicyDocument
    payer: str
    plan_name: Optional[str]
    state: Optional[str]
    policy_number: Optional[str]
    effective_date: Optional[str]
    last_reviewed_date: Optional[str]
    primary_drug: Optional[str]
    chunks: list[dict]
    tagged_chunks: list[dict]
    extracted: DrugCoverageExtractionResult


def parse_policy_bytes(file_bytes: bytes, source_name: str) -> PolicyDocument:
    return parse_pdf_bytes(file_bytes, source_name=source_name)


def parse_policy_path(path: str) -> PolicyDocument:
    return parse_pdf_path(path)


def run_policy_extraction(document: PolicyDocument, gemini: GeminiService) -> PolicyExtractionBundle:
    metadata = gemini.extract_policy_metadata(document)
    extracted = gemini.extract_policy_coverages(document, metadata)

    payer = extracted.payer or metadata.get("payer") or "Unknown"
    plan_name = metadata.get("plan_name") or metadata.get("policy_scope")
    state = metadata.get("state")
    policy_number = extracted.policy_number or metadata.get("policy_number")
    effective_date = extracted.effective_date or metadata.get("effective_date")
    last_reviewed_date = extracted.last_reviewed_date or metadata.get("last_reviewed_date")
    primary_drug = metadata.get("primary_drug")

    chunks = build_rag_chunks(document)
    tagged_chunks = gemini.tag_chunks_for_rag(chunks, payer, metadata=metadata, primary_drug=primary_drug)

    return PolicyExtractionBundle(
        document=document,
        payer=payer,
        plan_name=plan_name if isinstance(plan_name, str) else None,
        state=state if isinstance(state, str) else None,
        policy_number=policy_number,
        effective_date=effective_date,
        last_reviewed_date=last_reviewed_date,
        primary_drug=primary_drug,
        chunks=chunks,
        tagged_chunks=tagged_chunks,
        extracted=extracted,
    )
