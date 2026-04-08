from __future__ import annotations

import json
import re
import time
from typing import Dict, List, Optional, Union

import httpx

from app.config import Settings
from app.schemas.drug_coverage import DrugCoverageExtractionResult
from app.services.pdf_policy_parser import (
    PolicyDocument,
    build_extraction_chunks,
    build_program_extraction_chunks,
    extract_program_policy_backbone,
    first_pages_text,
    infer_primary_drug_hint,
)


class GeminiService:
    CHUNK_SIZE = 15000
    CHUNK_OVERLAP = 500
    MAX_EXTRACTION_CHUNKS = 18
    REQUEST_TIMEOUT_SECONDS = 120.0
    REQUEST_DELAY_SECONDS = 1.0

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def is_configured(self) -> bool:
        return bool(self.settings.gemini_api_key)

    def status(self) -> Dict[str, Union[str, bool]]:
        return {
            "configured": self.is_configured,
            "model": self.settings.gemini_model,
            "embedding_model": self.settings.gemini_embedding_model,
        }

    # -------------------------------------------------------------------------
    # Legacy chunking (kept for backward compat and RAG use)
    # -------------------------------------------------------------------------

    def chunk_text(self, raw_text: str) -> List[Dict]:
        lines = [line.strip() for line in raw_text.splitlines()]
        compact = "\n".join(line for line in lines if line)

        chunks = []
        start = 0
        idx = 0
        total = len(compact)

        while start < total:
            end = min(start + self.CHUNK_SIZE, total)
            if end < total:
                for sep in ["\n\n", "\n", ". ", " "]:
                    pos = compact.rfind(sep, start + self.CHUNK_SIZE // 2, end)
                    if pos != -1:
                        end = pos + len(sep)
                        break

            content = compact[start:end].strip()
            if content:
                estimated_page = (start // 3000) + 1
                section_type = self._detect_section_type(content)
                chunks.append({
                    "chunk_index": idx,
                    "content": content,
                    "page_number": estimated_page,
                    "section_type": section_type,
                })
                idx += 1

            if end >= total:
                break

            next_start = max(end - self.CHUNK_OVERLAP, start + 1)
            if next_start <= start:
                break
            start = next_start

        return chunks

    def _detect_section_type(self, text: str) -> str:
        lower = text.lower()
        if any(k in lower for k in ["prior authorization", "prior auth", "pa criteria", "authorization criteria"]):
            return "prior_auth"
        if any(k in lower for k in ["step therapy", "step edit", "fail first", "tried and failed"]):
            return "step_therapy"
        if any(k in lower for k in ["covered indication", "medically necessary", "diagnosis", "indication"]):
            return "indications"
        if any(k in lower for k in ["site of care", "site of service", "place of service", "infusion center", "physician office"]):
            return "site_of_care"
        if any(k in lower for k in ["quantity limit", "dose limit", "frequency limit"]):
            return "quantity_limit"
        return "general"

    # -------------------------------------------------------------------------
    # Policy-aware extraction
    # -------------------------------------------------------------------------

    def extract_policy_metadata(self, document: PolicyDocument) -> Dict[str, object]:
        payer = self._detect_payer_heuristic(document) or self.detect_payer(document.raw_text) or "Unknown"
        policy_number = self._extract_policy_number(document.raw_text)
        effective_date = self._extract_date(document.raw_text, ["effective date"])
        last_reviewed_date = self._extract_date(document.raw_text, ["last reviewed", "review date"])
        primary_drug = infer_primary_drug_hint(document)
        program_backbone = extract_program_policy_backbone(document) if document.document_type == "program_policy" else None

        metadata: Dict[str, object] = {
            "payer": payer,
            "policy_number": policy_number,
            "effective_date": effective_date,
            "last_reviewed_date": last_reviewed_date,
            "document_type": document.document_type,
            "policy_scope": document.title,
            "plan_name": document.title,
            "state": None,
            "primary_drug": primary_drug,
            "governed_drugs": self._infer_governed_drugs_heuristic(document),
            "program_backbone": program_backbone,
        }

        if program_backbone:
            metadata["payer"] = program_backbone.get("payer") or metadata["payer"]
            metadata["policy_number"] = program_backbone.get("policy_number") or metadata["policy_number"]
            metadata["effective_date"] = program_backbone.get("effective_date") or metadata["effective_date"]
            metadata["policy_scope"] = program_backbone.get("plan_name") or metadata["policy_scope"]
            metadata["plan_name"] = program_backbone.get("plan_name") or metadata["plan_name"]
            metadata["state"] = program_backbone.get("state")
            metadata["governed_drugs"] = program_backbone.get("governed_drugs") or metadata["governed_drugs"]

        if not self.is_configured or self._metadata_is_sufficient(metadata):
            metadata["governed_drugs"] = self._refine_governed_drugs(document, metadata)
            return metadata

        prompt = self._build_metadata_prompt(document, metadata)
        result = self._request_json(prompt, temperature=0.0, timeout=45.0, allow_quota_failure=True)
        if result:
            metadata["payer"] = result.get("payer") or metadata["payer"]
            metadata["policy_number"] = result.get("policy_number") or metadata["policy_number"]
            metadata["effective_date"] = result.get("effective_date") or metadata["effective_date"]
            metadata["last_reviewed_date"] = result.get("last_reviewed_date") or metadata["last_reviewed_date"]
            metadata["document_type"] = result.get("document_type") or metadata["document_type"]
            metadata["policy_scope"] = result.get("policy_scope") or metadata["policy_scope"]
            metadata["primary_drug"] = result.get("primary_drug") or metadata["primary_drug"]
            metadata["governed_drugs"] = result.get("governed_drugs") or metadata["governed_drugs"]

        metadata["governed_drugs"] = self._refine_governed_drugs(document, metadata)
        return metadata

    def extract_policy_coverages(
        self,
        document: PolicyDocument,
        metadata: Optional[Dict[str, object]] = None,
    ) -> DrugCoverageExtractionResult:
        if not self.is_configured:
            raise ValueError("Gemini API key is missing in backend/.env")

        metadata = metadata or self.extract_policy_metadata(document)
        if str(metadata.get("document_type")) == "program_policy":
            return self._extract_program_policy_coverages(document, metadata)

        jcode_map = self._extract_jcodes_from_text(document.raw_text)
        chunks = build_extraction_chunks(document)[:self.MAX_EXTRACTION_CHUNKS]

        merged_payer = metadata.get("payer")
        merged_policy_number = metadata.get("policy_number")
        merged_effective_date = metadata.get("effective_date")
        merged_last_reviewed = metadata.get("last_reviewed_date")

        coverage_map: Dict[str, dict] = {}

        for chunk in chunks:
            result = self._extract_policy_chunk(chunk, metadata)
            if not result:
                time.sleep(self.REQUEST_DELAY_SECONDS)
                continue

            if not merged_payer and result.get("payer"):
                merged_payer = result["payer"]
            if not merged_policy_number and result.get("policy_number"):
                merged_policy_number = result["policy_number"]
            if not merged_effective_date and result.get("effective_date"):
                merged_effective_date = result["effective_date"]
            if not merged_last_reviewed and result.get("last_reviewed_date"):
                merged_last_reviewed = result["last_reviewed_date"]

            for candidate in result.get("coverages") or []:
                normalized = self._normalize_candidate(candidate, chunk, jcode_map, document.document_type)
                if not normalized:
                    continue
                key = self._coverage_key(normalized, document.document_type)
                if key not in coverage_map:
                    coverage_map[key] = normalized
                else:
                    self._merge_coverage(coverage_map[key], normalized)

            time.sleep(self.REQUEST_DELAY_SECONDS)

        filtered_candidates = self._filter_candidates(
            list(coverage_map.values()),
            document=document,
            metadata=metadata,
        )

        payloads = [self._candidate_to_payload(candidate) for candidate in filtered_candidates]

        try:
            return DrugCoverageExtractionResult(
                payer=merged_payer if isinstance(merged_payer, str) else None,
                policy_number=merged_policy_number if isinstance(merged_policy_number, str) else None,
                effective_date=merged_effective_date if isinstance(merged_effective_date, str) else None,
                last_reviewed_date=merged_last_reviewed if isinstance(merged_last_reviewed, str) else None,
                coverages=payloads,
            )
        except Exception:
            return DrugCoverageExtractionResult(coverages=[])

    def verify_extraction(self, document: PolicyDocument, extraction: DrugCoverageExtractionResult) -> DrugCoverageExtractionResult:
        """Cross-verify extracted coverages with a second pass at higher temperature.
        Flags low-confidence entries and removes hallucinated drugs."""
        if not extraction.coverages or not self.is_configured:
            return extraction

        drug_names = [c.drug_name for c in extraction.coverages if c.drug_name]
        coverage_summary = ", ".join(
            "{0} ({1})".format(c.drug_name, c.coverage_status or "unknown")
            for c in extraction.coverages[:20]
        )

        prompt = """You are verifying drug extraction results from a medical policy PDF.
The document title is: {title}

The extraction found these drugs: {drugs}
Coverage summary: {summary}

Review the first 3000 characters of the document and answer in JSON:
{{
  "confirmed_drugs": ["list of drug names that genuinely appear in this document"],
  "hallucinated_drugs": ["list of drug names that do NOT appear in the document"],
  "corrections": [
    {{"drug": "name", "field": "field_name", "issue": "description"}}
  ]
}}

Document text (first 3000 chars):
{text}""".format(
            title=document.title or "Unknown",
            drugs=", ".join(drug_names[:20]),
            summary=coverage_summary,
            text=document.raw_text[:3000],
        )

        try:
            result = self._request_json(prompt, temperature=0.1, timeout=60.0, allow_quota_failure=True)
            if not result:
                return extraction

            hallucinated = set(d.lower() for d in (result.get("hallucinated_drugs") or []))
            if hallucinated:
                verified = [c for c in extraction.coverages if (c.drug_name or "").lower() not in hallucinated]
                return DrugCoverageExtractionResult(
                    payer=extraction.payer,
                    policy_number=extraction.policy_number,
                    effective_date=extraction.effective_date,
                    last_reviewed_date=extraction.last_reviewed_date,
                    coverages=verified,
                )
        except Exception:
            pass

        return extraction

    def extract_drug_coverages(self, raw_text: str) -> DrugCoverageExtractionResult:
        if not self.is_configured:
            raise ValueError("Gemini API key is missing in backend/.env")

        trimmed = raw_text.strip()
        if not trimmed:
            return DrugCoverageExtractionResult(coverages=[])

        fallback_document = PolicyDocument(
            title="Uploaded Policy Document",
            source_name="unknown.pdf",
            document_type="drug_policy",
            pages=[],
            raw_text=trimmed,
        )
        metadata = {
            "payer": self.detect_payer(trimmed) or "Unknown",
            "policy_number": self._extract_policy_number(trimmed),
            "effective_date": self._extract_date(trimmed, ["effective date"]),
            "last_reviewed_date": self._extract_date(trimmed, ["last reviewed", "review date"]),
            "document_type": "drug_policy",
            "policy_scope": "Uploaded Policy Document",
            "primary_drug": None,
            "governed_drugs": [],
        }

        chunks = self.chunk_text(trimmed)
        coverage_map: Dict[str, dict] = {}

        for chunk in chunks:
            result = self._extract_policy_chunk(chunk, metadata)
            if not result:
                time.sleep(self.REQUEST_DELAY_SECONDS)
                continue
            for candidate in result.get("coverages") or []:
                normalized = self._normalize_candidate(candidate, chunk, self._extract_jcodes_from_text(trimmed))
                if not normalized:
                    continue
                key = self._coverage_key(normalized)
                if key not in coverage_map:
                    coverage_map[key] = normalized
                else:
                    self._merge_coverage(coverage_map[key], normalized)

            time.sleep(self.REQUEST_DELAY_SECONDS)

        payloads = [self._candidate_to_payload(candidate) for candidate in coverage_map.values()]
        try:
            return DrugCoverageExtractionResult(
                payer=metadata["payer"],
                policy_number=metadata["policy_number"],
                effective_date=metadata["effective_date"],
                last_reviewed_date=metadata["last_reviewed_date"],
                coverages=payloads,
            )
        except Exception:
            return DrugCoverageExtractionResult(coverages=[])

    def _merge_coverage(self, existing: dict, incoming: dict) -> None:
        list_fields = [
            "brand_names",
            "covered_indications",
            "prior_auth_criteria",
            "step_therapy_requirements",
            "site_of_care",
            "source_pages",
        ]
        for field in list_fields:
            existing_vals = existing.get(field) or []
            incoming_vals = incoming.get(field) or []
            merged = list(dict.fromkeys(existing_vals + incoming_vals))
            if merged:
                existing[field] = merged

        scalar_fields = [
            "generic_name",
            "family_name",
            "product_name",
            "product_key",
            "policy_name",
            "document_type",
            "hcpcs_code",
            "drug_tier",
            "quantity_limit_detail",
            "prescriber_requirements",
            "coverage_status",
            "coverage_bucket",
            "notes",
            "evidence_snippet",
            "source_section",
        ]
        for field in scalar_fields:
            if not existing.get(field) and incoming.get(field):
                existing[field] = incoming[field]

        for field in ["prior_authorization", "quantity_limit", "step_therapy"]:
            if incoming.get(field):
                existing[field] = True

        if (incoming.get("confidence_score") or 0) > (existing.get("confidence_score") or 0):
            existing["confidence_score"] = incoming["confidence_score"]

    def _extract_policy_chunk(self, chunk: dict, metadata: Dict[str, object]) -> Optional[dict]:
        prompt = self._build_policy_chunk_prompt(chunk, metadata)
        return self._request_json(
            prompt,
            temperature=0.0,
            timeout=self.REQUEST_TIMEOUT_SECONDS,
            allow_quota_failure=True,
        )

    # -------------------------------------------------------------------------
    # RAG helpers
    # -------------------------------------------------------------------------

    def tag_chunks_for_rag(
        self,
        chunks: List[Dict],
        payer: str,
        metadata: Optional[Dict[str, object]] = None,
        primary_drug: Optional[str] = None,
    ) -> List[Dict]:
        metadata = metadata or {}
        for chunk in chunks:
            chunk["payer"] = payer
            tagged_drug, matched_alias = self._infer_chunk_drug(chunk.get("content") or "", metadata, primary_drug)
            chunk["drug_name"] = tagged_drug or primary_drug
            chunk["metadata"] = {
                "matched_alias": matched_alias,
                "policy_name": metadata.get("plan_name") or metadata.get("policy_scope"),
                "document_type": metadata.get("document_type"),
            }
        return chunks

    def _identify_drug_in_chunk(self, text: str) -> Optional[str]:
        prompt = (
            "What is the primary drug name (generic preferred) discussed in this text? "
            "Reply with ONLY the drug name as a single word or short phrase, nothing else. "
            "If no specific drug is mentioned, reply with null.\n\nText:\n" + text[:3000]
        )
        try:
            text = self._request_text(prompt, temperature=0.0, timeout=30.0).strip().lower()
            if text in ("null", "none", "", "n/a"):
                return None
            return text[:100]
        except Exception:
            return None

    # -------------------------------------------------------------------------
    # RAG question answering
    # -------------------------------------------------------------------------

    _GREETING_WORDS = {"hello", "hi", "hey", "howdy", "greetings", "sup", "yo", "hola", "thanks", "thank you", "bye", "goodbye"}

    def _is_greeting(self, question: str) -> bool:
        cleaned = question.strip().rstrip("!?.").lower()
        return cleaned in self._GREETING_WORDS or len(cleaned.split()) <= 2 and cleaned.split()[0] in self._GREETING_WORDS

    def ask_question_rag(self, question: str, chunks: List[Dict]) -> dict:
        if not self.is_configured:
            raise ValueError("Gemini API key is missing in backend/.env")

        # Handle greetings and non-policy questions
        if self._is_greeting(question):
            return {
                "answer": "Hi! I'm RxPulse AI. Ask me anything about the medical benefit drug policies in this system — coverage criteria, prior authorization, step therapy, biosimilar requirements, site-of-care restrictions, and more.",
                "sources": [],
                "drugs_found": [],
            }

        if not chunks:
            return {
                "answer": "I don't have enough policy data to answer that question. Try uploading more policy documents first.",
                "sources": [],
                "drugs_found": [],
            }

        sources = set()
        drugs_found = set()
        context_parts = []

        for chunk in chunks[:12]:
            payer = chunk.get("payer") or "Unknown payer"
            drug = chunk.get("drug_name") or ""
            section = chunk.get("section_type") or "general"
            content = chunk.get("content") or ""

            header = "[{payer}]".format(payer=payer)
            if drug:
                header += " Drug: {drug}".format(drug=drug)
            if section != "general":
                header += " | Section: {section}".format(section=section)

            context_parts.append("{0}\n{1}".format(header, content[:2500]))
            sources.add(payer)
            if drug:
                drugs_found.add(drug)

        prompt = """You are RxPulse, a medical benefit drug policy assistant for healthcare analysts.
Answer the question using ONLY the policy text provided below.

CRITICAL RULES:
1. Always cite the specific payer name (e.g., "According to Cigna..." or "BCBS NC requires...").
2. Be EXHAUSTIVE — list ALL criteria, ALL biosimilars, ALL required conditions. Never summarize with "such as" or "including" when you can list every item.
3. When a question asks about specific requirements, list each one as a numbered item.
4. If multiple payers have relevant data, answer for EACH payer separately.
5. If the policy text mentions specific section numbers, reference them.
6. Do not confuse one payer's criteria with another — each payer section is labeled with [Payer Name].
7. If the provided text does not contain information to answer the question, say "The uploaded policies do not contain information about this topic" — do not guess or use general medical knowledge.
8. Do not use markdown bold (**) or headers — plain text only.

Policy text:
{context}

Question: {question}""".format(context="\n\n---\n\n".join(context_parts), question=question)

        try:
            answer = self._request_text(prompt, temperature=0.1, timeout=self.REQUEST_TIMEOUT_SECONDS)
        except Exception as exc:
            answer = "Error generating answer: {0}".format(str(exc))

        return {
            "answer": answer,
            "sources": sorted(sources),
            "drugs_found": sorted(drug for drug in drugs_found if drug),
        }

    # -------------------------------------------------------------------------
    # Diff / comparison
    # -------------------------------------------------------------------------

    def diff_policy_documents(
        self,
        rows_a: list,
        rows_b: list,
        label_a: str = "Document A",
        label_b: str = "Document B",
    ) -> dict:
        if not self.is_configured:
            raise ValueError("Gemini API key is missing in backend/.env")

        def _summarize(rows: list) -> str:
            lines = []
            for row in rows[:30]:
                parts = [row.get("drug_name", "unknown")]
                if row.get("hcpcs_code"):
                    parts.append("J-code:{0}".format(row["hcpcs_code"]))
                parts.append("coverage:{0}".format(row.get("coverage_status") or "unknown"))
                if row.get("prior_authorization"):
                    criteria = "; ".join(row.get("prior_auth_criteria") or []) or "yes"
                    parts.append("prior_auth:{0}".format(criteria))
                if row.get("step_therapy"):
                    reqs = "; ".join(row.get("step_therapy_requirements") or []) or "yes"
                    parts.append("step_therapy:{0}".format(reqs))
                if row.get("covered_indications"):
                    parts.append("indications:{0}".format("; ".join(row["covered_indications"])))
                if row.get("site_of_care"):
                    parts.append("site:{0}".format(", ".join(row["site_of_care"])))
                lines.append(" | ".join(parts))
            return "\n".join(lines)

        prompt = """You are a medical policy analyst comparing two insurance drug policy documents.
Document A is the OLDER policy ({label_a}).
Document B is the NEWER policy ({label_b}).

Return ONLY valid JSON:
{{
  "summary": "2-3 sentence plain English summary of what changed",
  "net_impact": "more_restrictive | less_restrictive | mixed | unchanged",
  "patient_impact_summary": "plain English: what does this mean for patients?",
  "changes": [
    {{
      "drug_name": "string",
      "field": "prior_auth_criteria | step_therapy | covered_indications | site_of_care | coverage_status | other",
      "change_type": "added | removed | modified",
      "old_value": "string or null",
      "new_value": "string or null",
      "impact": "more_restrictive | less_restrictive | neutral"
    }}
  ]
}}

DOCUMENT A ({label_a}):
{rows_a}

DOCUMENT B ({label_b}):
{rows_b}
""".format(label_a=label_a, label_b=label_b, rows_a=_summarize(rows_a), rows_b=_summarize(rows_b))

        try:
            return self._request_json(prompt, temperature=0.1, timeout=self.REQUEST_TIMEOUT_SECONDS)
        except Exception:
            return {
                "summary": "Unable to parse differences.",
                "net_impact": "unknown",
                "patient_impact_summary": "",
                "changes": [],
            }

    # -------------------------------------------------------------------------
    # Payer detection
    # -------------------------------------------------------------------------

    def detect_payer(self, raw_text: str) -> Optional[str]:
        heuristic = self._detect_payer_heuristic_from_text(raw_text)
        if heuristic:
            return heuristic
        if not self.is_configured:
            return None

        sample = raw_text[:3000]
        prompt = (
            "What is the insurance company / health plan name (payer) that published this medical policy document? "
            "Reply with ONLY the payer name (e.g. 'UnitedHealthcare', 'Cigna', 'Blue Cross Blue Shield of North Carolina'). "
            "If unknown, reply with null.\n\nDocument text:\n" + sample
        )
        try:
            name = self._request_text(prompt, temperature=0.0, timeout=30.0).strip()
            if name.lower() in ("null", "none", "unknown", ""):
                return None
            return name
        except Exception:
            return None

    # -------------------------------------------------------------------------
    # Embeddings (for vector search)
    # -------------------------------------------------------------------------

    def embed_text(self, text: str) -> Optional[List[float]]:
        """Generate a 768-dim embedding using Gemini text-embedding-004."""
        if not self.is_configured:
            return None
        try:
            response = httpx.post(
                "https://generativelanguage.googleapis.com/v1beta/models/{0}:embedContent".format(
                    self.settings.gemini_embedding_model
                ),
                params={"key": self.settings.gemini_api_key},
                json={
                    "model": "models/{0}".format(self.settings.gemini_embedding_model),
                    "content": {"parts": [{"text": text[:2048]}]},
                },
                timeout=30.0,
            )
            response.raise_for_status()
            return response.json().get("embedding", {}).get("values")
        except Exception:
            return None

    def embed_texts_batch(self, texts: List[str]) -> List[Optional[List[float]]]:
        """Embed multiple texts, one at a time (Gemini has no batch embed endpoint for this model)."""
        results = []
        for text in texts:
            results.append(self.embed_text(text))
            time.sleep(0.1)  # rate limit
        return results

    # -------------------------------------------------------------------------
    # Request helpers
    # -------------------------------------------------------------------------

    def _request_text(self, prompt: str, temperature: float, timeout: float) -> str:
        response = httpx.post(
            "https://generativelanguage.googleapis.com/v1beta/models/{0}:generateContent".format(
                self.settings.gemini_model
            ),
            params={"key": self.settings.gemini_api_key},
            json={
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": temperature},
            },
            timeout=timeout,
        )
        response.raise_for_status()
        return self._extract_response_text(response.json())

    def _request_json(self, prompt: str, temperature: float, timeout: float, allow_quota_failure: bool = False) -> Dict:
        try:
            response = httpx.post(
                "https://generativelanguage.googleapis.com/v1beta/models/{0}:generateContent".format(
                    self.settings.gemini_model
                ),
                params={"key": self.settings.gemini_api_key},
                json={
                    "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "responseMimeType": "application/json",
                        "temperature": temperature,
                    },
                },
                timeout=timeout,
            )
            response.raise_for_status()
            return self._parse_json_payload(self._extract_response_text(response.json()))
        except httpx.HTTPStatusError as exc:
            if allow_quota_failure and exc.response.status_code == 429:
                return {}
            raise

    # -------------------------------------------------------------------------
    # Prompt builders
    # -------------------------------------------------------------------------

    def _build_metadata_prompt(self, document: PolicyDocument, seed: Dict[str, object]) -> str:
        return """You are reviewing a medical-benefit drug policy PDF.
Return ONLY valid JSON in this exact shape:
{{
  "payer": "string or null",
  "policy_number": "string or null",
  "effective_date": "YYYY-MM-DD or null",
  "last_reviewed_date": "YYYY-MM-DD or null",
  "document_type": "drug_policy | program_policy | formulary_list | unknown",
  "policy_scope": "short plain-English summary of what this document governs",
  "primary_drug": "generic/family name or null",
  "governed_drugs": [
    {{
      "drug_name": "generic or family name",
      "brand_names": ["brand name strings"]
    }}
  ]
}}

Rules:
- Governed drugs are only the products the policy actually governs.
- Exclude comparator therapies, prerequisite drugs, literature citations, and examples.
- Use the provided heuristic values unless the document clearly shows something more specific.
- For multi-product program policies, include each governed family or product group.

Heuristic seed:
{seed}

Document title:
{title}

Opening pages:
{opening_pages}
""".format(
            seed=json.dumps(seed, indent=2),
            title=document.title,
            opening_pages=first_pages_text(document),
        )

    # Payer-specific extraction hints — each payer formats policies differently
    PAYER_EXTRACTION_HINTS = {
        "bcbs": (
            "BCBS/Blue Cross policies typically use 'Corporate Medical Policy' format with "
            "sections: Policy Statement, Description, Rationale, Coding, References. "
            "Coverage criteria are in 'Policy Statement'. Look for 'Preferred Injectable' program lists. "
            "Products are categorized as preferred/non-preferred with HCPCS J-codes in Coding sections."
        ),
        "uhc": (
            "UnitedHealthcare policies use 'Medical Benefit Drug Policy' format. "
            "Key sections: Coverage Rationale, Definitions, Applicable Codes, References. "
            "Look for 'proven' vs 'unproven' indications. UHC often lists specific J-codes and "
            "quantity limits in 'Applicable Codes'. Step therapy requirements are in Coverage Rationale."
        ),
        "cigna": (
            "Cigna policies use 'Coverage Policy' format with sections: Coverage Policy, "
            "General Background, Coding/Billing, References. Cigna separates 'medically necessary' "
            "from 'experimental/investigational/unproven'. Look for site-of-care requirements "
            "and biosimilar substitution policies."
        ),
        "aetna": (
            "Aetna policies use 'Clinical Policy Bulletin' format. Key sections: Policy, Background, "
            "Coding, References. Aetna often has detailed step-therapy requirements and uses "
            "'medically necessary' vs 'experimental and investigational' classification."
        ),
        "humana": (
            "Humana policies use 'Medical Coverage Policy' format. Look for Prior Authorization "
            "requirements, quantity limits, and site-of-care restrictions in the Coverage section."
        ),
    }

    def _get_payer_hint(self, payer: Optional[str]) -> str:
        if not payer:
            return ""
        payer_lower = payer.lower()
        for key, hint in self.PAYER_EXTRACTION_HINTS.items():
            if key in payer_lower:
                return "\n\nPayer-specific guidance:\n" + hint
        # Check common aliases
        if any(k in payer_lower for k in ["blue cross", "blue shield", "anthem"]):
            return "\n\nPayer-specific guidance:\n" + self.PAYER_EXTRACTION_HINTS["bcbs"]
        if any(k in payer_lower for k in ["united", "optum"]):
            return "\n\nPayer-specific guidance:\n" + self.PAYER_EXTRACTION_HINTS["uhc"]
        return ""

    def _build_policy_chunk_prompt(self, chunk: dict, metadata: Dict[str, object]) -> str:
        document_type = metadata.get("document_type") or "drug_policy"
        governed_drugs = metadata.get("governed_drugs") or []
        policy_scope = metadata.get("policy_scope") or "medical benefit drug policy"

        if document_type == "program_policy":
            scope_rules = (
                "Only extract products explicitly governed by the program, such as preferred, non-preferred, restricted, "
                "proven, unproven, or program-listed products. Ignore drugs mentioned only in references, clinical evidence, "
                "combination regimens, or examples."
            )
            secondary_rules = (
                "For program policies, FDA-approved use text can enrich an existing governed product row but must not create a new row by itself. "
                "A product row should only be returned if the chunk also contains program evidence such as preferred/non-preferred placement, "
                "restricted product language, medically necessary criteria, proven/unproven status, or product-specific requirements."
            )
        else:
            scope_rules = (
                "Only extract drugs that belong to the governed policy family or named covered products. "
                "Ignore prerequisite drugs, comparator drugs, references, and therapies mentioned only as background."
            )
            secondary_rules = (
                "If the chunk is background-only or literature-heavy, return an empty coverages list."
            )

        return """You are extracting structured coverage data from a medical-benefit drug policy chunk.

Document metadata:
{metadata}

Chunk page numbers: {page_numbers}
Chunk section type: {section_type}
Policy scope: {policy_scope}
Governed drugs: {governed_drugs}
Focused family for this chunk: {family_name}
Family aliases for this chunk: {family_aliases}
Target product for this chunk: {target_product}

Return ONLY valid JSON in this exact shape:
{{
  "payer": "string or null",
  "policy_number": "string or null",
  "effective_date": "YYYY-MM-DD or null",
  "last_reviewed_date": "YYYY-MM-DD or null",
  "coverages": [
    {{
      "drug_name": "string (generic name preferred)",
      "brand_names": ["string"],
      "hcpcs_code": "J1234 or null",
      "drug_tier": "preferred | non_preferred | excluded | not_applicable | null",
      "covered_indications": ["string"],
      "prior_authorization": true,
      "prior_auth_criteria": ["string"],
      "quantity_limit": false,
      "quantity_limit_detail": "string or null",
      "step_therapy": false,
      "step_therapy_requirements": ["string"],
      "site_of_care": ["hospital", "office", "home"],
      "prescriber_requirements": "string or null",
      "coverage_status": "covered | not_covered | restricted | unknown",
      "notes": "short plain-English note or null",
      "confidence_score": 0.9,
      "source_pages": [1],
      "source_section": "coverage | indications | coding | general",
      "evidence_snippet": "very short quote or paraphrase from this chunk"
    }}
  ]
}}

Rules:
- {scope_rules}
- {secondary_rules}
- Prefer rows supported by coverage, criteria, preferred/non-preferred, or coding language.
- If this chunk is mostly references, revision history, or evidence review, return {{"coverages": []}}.
- If a chunk mentions a drug only as a step-therapy prerequisite, background comparator, or literature example, do not create a row for it.
- Use "restricted" when coverage requires PA, preferred-product failure, product-specific criteria, or step therapy.
- Use "not_covered" only when the policy clearly says excluded, non-covered, or not medically necessary.
- Keep evidence_snippet short.
- Do not include extra keys.
{payer_hint}
Chunk text:
{chunk_text}
""".format(
            metadata=json.dumps({
                "payer": metadata.get("payer"),
                "policy_number": metadata.get("policy_number"),
                "document_type": metadata.get("document_type"),
                "primary_drug": metadata.get("primary_drug"),
            }, indent=2),
            page_numbers=chunk.get("page_numbers") or [chunk.get("page_number")],
            section_type=chunk.get("section_type") or "general",
            policy_scope=policy_scope,
            governed_drugs=json.dumps(governed_drugs, indent=2),
            family_name=chunk.get("family_name") or "all governed products",
            family_aliases=", ".join(chunk.get("family_aliases") or []),
            target_product=json.dumps(metadata.get("target_product") or {}, indent=2),
            scope_rules=scope_rules,
            secondary_rules=secondary_rules,
            payer_hint=self._get_payer_hint(metadata.get("payer")),
            chunk_text=chunk.get("content") or "",
        )

    def _build_program_chunks(self, document: PolicyDocument, metadata: Dict[str, object]) -> List[dict]:
        base_chunks = build_program_extraction_chunks(document)
        if not base_chunks:
            return build_extraction_chunks(document)[:self.MAX_EXTRACTION_CHUNKS]

        aliases = self._build_scope_aliases(metadata, document)
        family_alias_groups = self._build_family_alias_groups(metadata, document)
        scoped_chunks: List[dict] = []
        chunk_index = 0

        for family_name, family_aliases in family_alias_groups:
            family_aliases = {alias for alias in family_aliases if alias}
            matching_chunks = []
            for chunk in base_chunks:
                content = (chunk.get("content") or "").lower()
                if any(alias in content for alias in family_aliases):
                    family_chunk = dict(chunk)
                    family_chunk["family_name"] = family_name
                    family_chunk["family_aliases"] = sorted(family_aliases)
                    family_chunk["chunk_index"] = chunk_index
                    matching_chunks.append(family_chunk)
                    chunk_index += 1
            if matching_chunks:
                scoped_chunks.extend(matching_chunks[:3])

        if not scoped_chunks:
            scoped_chunks = []
            for chunk in base_chunks[:self.MAX_EXTRACTION_CHUNKS]:
                scoped_chunk = dict(chunk)
                scoped_chunk["family_name"] = None
                scoped_chunk["family_aliases"] = sorted(aliases)
                scoped_chunks.append(scoped_chunk)

        return scoped_chunks[:self.MAX_EXTRACTION_CHUNKS]

    def _extract_program_policy_coverages(
        self,
        document: PolicyDocument,
        metadata: Dict[str, object],
    ) -> DrugCoverageExtractionResult:
        backbone = metadata.get("program_backbone") if isinstance(metadata.get("program_backbone"), dict) else None
        if backbone and backbone.get("products"):
            structure_rows = [
                self._normalize_program_backbone_row(row)
                for row in backbone.get("products") or []
            ]
            structure_rows = [row for row in structure_rows if row]
        else:
            jcode_map = self._extract_jcodes_from_text(document.raw_text)
            structure_rows = self._extract_program_structure_rows(document, metadata, jcode_map)
        coverage_map: Dict[str, dict] = {}

        for row in structure_rows:
            key = self._coverage_key(row, document.document_type)
            coverage_map[key] = row

        should_enrich = bool(self.is_configured and not backbone)
        if should_enrich:
            jcode_map = self._extract_jcodes_from_text(document.raw_text)
            for row in structure_rows:
                targeted_metadata = dict(metadata)
                targeted_metadata["target_product"] = {
                    "drug_name": row.get("drug_name"),
                    "brand_names": row.get("brand_names") or [],
                    "product_key": row.get("product_key"),
                }
                for chunk in self._build_product_enrichment_chunks(document, row):
                    result = self._extract_policy_chunk(chunk, targeted_metadata)
                    if not result:
                        time.sleep(self.REQUEST_DELAY_SECONDS)
                        continue
                    for candidate in result.get("coverages") or []:
                        candidate.setdefault("product_key", row.get("product_key"))
                        normalized = self._normalize_candidate(candidate, chunk, jcode_map, document.document_type)
                        if not normalized:
                            continue
                        key = self._coverage_key(normalized, document.document_type)
                        if key not in coverage_map:
                            coverage_map[key] = normalized
                        else:
                            self._merge_coverage(coverage_map[key], normalized)
                    time.sleep(self.REQUEST_DELAY_SECONDS)

        filtered_candidates = self._filter_candidates(
            list(coverage_map.values()),
            document=document,
            metadata=metadata,
        )
        payloads = [self._candidate_to_payload(candidate) for candidate in filtered_candidates]

        try:
            return DrugCoverageExtractionResult(
                payer=metadata.get("payer") if isinstance(metadata.get("payer"), str) else None,
                policy_number=metadata.get("policy_number") if isinstance(metadata.get("policy_number"), str) else None,
                effective_date=metadata.get("effective_date") if isinstance(metadata.get("effective_date"), str) else None,
                last_reviewed_date=metadata.get("last_reviewed_date") if isinstance(metadata.get("last_reviewed_date"), str) else None,
                coverages=payloads,
            )
        except Exception:
            return DrugCoverageExtractionResult(coverages=[])

    def _extract_program_structure_rows(
        self,
        document: PolicyDocument,
        metadata: Dict[str, object],
        jcode_map: Dict[str, str],
    ) -> List[dict]:
        pages = [page for page in document.pages if page.page_number <= 4 or page.section_type in {"coverage", "coding"}]
        key_text = "\n\n".join(page.text for page in pages)
        rows: List[dict] = []

        for family in metadata.get("governed_drugs") or []:
            if not isinstance(family, dict):
                continue
            generic_name = self._canonicalize_name(family.get("drug_name") or "")
            for brand in family.get("brand_names") or []:
                pages_for_product = self._find_relevant_pages_for_product(document, brand, generic_name)
                row = {
                    "drug_name": generic_name or self._canonicalize_name(brand),
                    "generic_name": generic_name or self._canonicalize_name(brand),
                    "family_name": generic_name or self._canonicalize_name(brand),
                    "product_name": brand,
                    "brand_names": [brand],
                    "hcpcs_code": self._find_code_for_product(brand, generic_name, key_text, jcode_map),
                    "drug_tier": self._infer_program_tier(brand, generic_name, pages),
                    "covered_indications": [],
                    "prior_authorization": False,
                    "prior_auth_criteria": [],
                    "quantity_limit": False,
                    "quantity_limit_detail": None,
                    "step_therapy": False,
                    "step_therapy_requirements": [],
                    "site_of_care": [],
                    "prescriber_requirements": None,
                    "coverage_status": "unknown",
                    "notes": self._infer_program_notes(brand, generic_name, pages),
                    "confidence_score": 0.72,
                    "source_pages": pages_for_product,
                    "source_section": "coverage",
                    "evidence_snippet": self._extract_evidence_snippet(brand, generic_name, pages),
                    "product_key": self._canonicalize_name(brand, preserve_brand=True),
                    "policy_name": metadata.get("plan_name") or document.title,
                    "document_type": "program_policy",
                }
                self._apply_program_policy_overrides(document, row)
                row["coverage_status"] = self._normalize_coverage_status(row)
                row["coverage_bucket"] = self._derive_coverage_bucket(row)
                rows.append(row)

        return rows

    def _normalize_program_backbone_row(self, row: dict) -> Optional[dict]:
        brand_names = [name for name in (row.get("brand_names") or []) if isinstance(name, str) and name.strip()]
        base_name = row.get("drug_name") or (brand_names[0] if brand_names else "")
        drug_name = self._canonicalize_name(str(base_name))
        if not drug_name:
            return None

        notes = self._normalize_optional_string(row.get("notes"))
        all_codes = [self._normalize_jcode(code) or str(code).upper() for code in (row.get("all_hcpcs_codes") or []) if code]
        all_codes = [code for code in all_codes if code]
        if len(all_codes) > 1 and "Additional HCPCS codes listed:" not in (notes or ""):
            extra_note = "Additional HCPCS codes listed: {0}.".format(", ".join(all_codes))
            notes = self._append_note(notes, extra_note)

        normalized = {
            "drug_name": drug_name,
            "generic_name": self._canonicalize_name(row.get("generic_name") or drug_name),
            "family_name": self._canonicalize_name(row.get("family_name") or drug_name),
            "product_name": self._normalize_optional_string(row.get("product_name")) or (brand_names[0] if brand_names else None),
            "brand_names": list(dict.fromkeys(brand_names)),
            "hcpcs_code": self._normalize_jcode(row.get("hcpcs_code")) or self._normalize_optional_string(row.get("hcpcs_code")),
            "drug_tier": row.get("drug_tier"),
            "covered_indications": self._normalize_string_list(row.get("covered_indications")),
            "prior_authorization": bool(row.get("prior_authorization")),
            "prior_auth_criteria": self._normalize_string_list(row.get("prior_auth_criteria")),
            "quantity_limit": bool(row.get("quantity_limit")),
            "quantity_limit_detail": self._normalize_optional_string(row.get("quantity_limit_detail")),
            "step_therapy": bool(row.get("step_therapy")),
            "step_therapy_requirements": self._normalize_string_list(row.get("step_therapy_requirements")),
            "site_of_care": self._normalize_site_of_care(row.get("site_of_care") or []),
            "prescriber_requirements": self._normalize_optional_string(row.get("prescriber_requirements")),
            "coverage_status": self._normalize_coverage_status(row),
            "coverage_bucket": row.get("coverage_bucket") or self._derive_coverage_bucket(row),
            "notes": notes,
            "confidence_score": min(max(float(row.get("confidence_score") or 0.85), 0.0), 1.0),
            "source_pages": [int(p) for p in (row.get("source_pages") or [1]) if isinstance(p, int)],
            "source_section": row.get("source_section") or "coverage",
            "evidence_snippet": self._normalize_optional_string(row.get("evidence_snippet")),
            "product_key": row.get("product_key") or self._derive_product_key(brand_names, "program_policy"),
            "policy_name": self._normalize_optional_string(row.get("policy_name")),
            "document_type": row.get("document_type") or "program_policy",
        }
        return normalized

    def _build_product_enrichment_chunks(self, document: PolicyDocument, row: dict) -> List[dict]:
        aliases = {
            str(row.get("drug_name") or "").lower(),
            str(row.get("product_key") or "").lower(),
        }
        aliases.update(str(brand).lower() for brand in (row.get("brand_names") or []))
        pages = []
        for page in document.pages:
            text = page.text.lower()
            if any(alias and alias in text for alias in aliases):
                pages.append(page)

        chunks: List[dict] = []
        current_parts: List[str] = []
        current_pages: List[int] = []
        current_sections: List[str] = []

        for page in pages[:4]:
            block = "Page {0} | Section: {1}\n{2}".format(page.page_number, page.section_type, page.text.strip())
            projected = sum(len(part) for part in current_parts) + len(block)
            if current_parts and projected > 4500:
                chunks.append({
                    "chunk_index": len(chunks),
                    "content": "\n\n".join(current_parts),
                    "page_number": current_pages[0],
                    "page_numbers": list(current_pages),
                    "section_type": self._dominant_section(current_sections),
                    "family_name": row.get("drug_name"),
                    "family_aliases": sorted(aliases),
                })
                current_parts = []
                current_pages = []
                current_sections = []

            current_parts.append(block)
            current_pages.append(page.page_number)
            current_sections.append(page.section_type)

        if current_parts:
            chunks.append({
                "chunk_index": len(chunks),
                "content": "\n\n".join(current_parts),
                "page_number": current_pages[0],
                "page_numbers": list(current_pages),
                "section_type": self._dominant_section(current_sections),
                "family_name": row.get("drug_name"),
                "family_aliases": sorted(aliases),
            })

        return chunks[:3]

    # -------------------------------------------------------------------------
    # Filtering and normalization
    # -------------------------------------------------------------------------

    def _normalize_candidate(
        self,
        candidate: dict,
        chunk: dict,
        jcode_map: Dict[str, str],
        document_type: str = "drug_policy",
    ) -> Optional[dict]:
        drug_name = (candidate.get("drug_name") or "").strip()
        brand_names = [name.strip() for name in (candidate.get("brand_names") or []) if isinstance(name, str) and name.strip()]
        if not drug_name and not brand_names:
            return None

        canonical_drug = self._canonicalize_name(drug_name or brand_names[0])
        if not canonical_drug:
            return None

        source_pages = [int(p) for p in (candidate.get("source_pages") or chunk.get("page_numbers") or [chunk.get("page_number")]) if isinstance(p, int)]
        if not source_pages:
            source_pages = [chunk.get("page_number", 1)]

        normalized = {
            "drug_name": canonical_drug,
            "generic_name": self._canonicalize_name(candidate.get("generic_name") or canonical_drug),
            "family_name": self._canonicalize_name(candidate.get("family_name") or canonical_drug),
            "product_name": self._normalize_optional_string(candidate.get("product_name")) or (brand_names[0] if brand_names else drug_name or canonical_drug),
            "product_key": candidate.get("product_key") or self._derive_product_key(brand_names, document_type),
            "policy_name": self._normalize_optional_string(candidate.get("policy_name")),
            "document_type": document_type,
            "brand_names": list(dict.fromkeys(brand_names)),
            "hcpcs_code": self._normalize_jcode(candidate.get("hcpcs_code")),
            "drug_tier": candidate.get("drug_tier"),
            "covered_indications": self._normalize_string_list(candidate.get("covered_indications")),
            "prior_authorization": bool(candidate.get("prior_authorization")),
            "prior_auth_criteria": self._normalize_string_list(candidate.get("prior_auth_criteria")),
            "quantity_limit": bool(candidate.get("quantity_limit")),
            "quantity_limit_detail": self._normalize_optional_string(candidate.get("quantity_limit_detail")),
            "step_therapy": bool(candidate.get("step_therapy")),
            "step_therapy_requirements": self._normalize_string_list(candidate.get("step_therapy_requirements")),
            "site_of_care": self._normalize_site_of_care(candidate.get("site_of_care") or []),
            "prescriber_requirements": self._normalize_optional_string(candidate.get("prescriber_requirements")),
            "coverage_status": self._normalize_coverage_status(candidate),
            "coverage_bucket": self._derive_coverage_bucket(candidate),
            "notes": self._normalize_optional_string(candidate.get("notes")),
            "confidence_score": min(max(float(candidate.get("confidence_score") or 0.5), 0.0), 1.0),
            "source_pages": source_pages,
            "source_section": candidate.get("source_section") or chunk.get("section_type") or "general",
            "evidence_snippet": self._normalize_optional_string(candidate.get("evidence_snippet")),
        }

        if not normalized["hcpcs_code"]:
            for token in [canonical_drug] + normalized["brand_names"]:
                for piece in self._tokenize_name(token):
                    if piece in jcode_map:
                        normalized["hcpcs_code"] = jcode_map[piece]
                        break
                if normalized["hcpcs_code"]:
                    break

        return normalized

    def _filter_candidates(self, candidates: List[dict], document: PolicyDocument, metadata: Dict[str, object]) -> List[dict]:
        scope_aliases = self._build_scope_aliases(metadata, document)
        filtered = []

        for candidate in candidates:
            if not self._has_structured_value(candidate):
                continue

            if candidate.get("source_section") in {"references", "revision_history", "instructions", "evidence_summary"}:
                continue

            if scope_aliases and not self._candidate_matches_scope(candidate, scope_aliases):
                continue

            if document.document_type == "drug_policy" and not self._candidate_is_drug_policy_relevant(candidate, metadata):
                continue

            if document.document_type == "program_policy" and not self._candidate_is_program_policy_relevant(candidate):
                continue

            if candidate.get("coverage_status") == "unknown" and not candidate.get("hcpcs_code") and not candidate.get("prior_auth_criteria"):
                continue

            filtered.append(candidate)

        filtered.sort(
            key=lambda item: (
                -(item.get("confidence_score") or 0),
                item.get("drug_name") or "",
            )
        )
        return filtered

    def _candidate_matches_scope(self, candidate: dict, scope_aliases: set[str]) -> bool:
        names = {self._canonicalize_name(candidate.get("drug_name") or "")}
        names.update(self._canonicalize_name(name) for name in (candidate.get("brand_names") or []))
        if candidate.get("product_key"):
            names.add(str(candidate.get("product_key")))
        names = {name for name in names if name}
        return bool(names & scope_aliases)

    def _candidate_is_drug_policy_relevant(self, candidate: dict, metadata: Dict[str, object]) -> bool:
        primary = self._canonicalize_name(str(metadata.get("primary_drug") or ""))
        if not primary:
            return True
        if primary == "botulinum toxins":
            return True

        names = {self._canonicalize_name(candidate.get("drug_name") or "")}
        names.update(self._canonicalize_name(name) for name in (candidate.get("brand_names") or []))
        if candidate.get("product_key"):
            names.add(str(candidate.get("product_key")))
        names = {name for name in names if name}

        if primary in names:
            return True

        if primary == "rituximab":
            return any(name.startswith("ritux") or name == "rituximab" for name in names)
        if primary == "bevacizumab":
            return any(name.startswith("beva") or name in {"avastin", "mvasi", "zirabev"} for name in names)
        return False

    def _candidate_is_program_policy_relevant(self, candidate: dict) -> bool:
        source_section = candidate.get("source_section")
        if source_section == "indications" and not any([
            candidate.get("drug_tier") and candidate.get("drug_tier") != "not_applicable",
            candidate.get("prior_authorization"),
            candidate.get("step_therapy"),
            candidate.get("hcpcs_code"),
            candidate.get("coverage_status") in {"restricted", "not_covered"},
        ]):
            return False

        notes = " ".join([
            str(candidate.get("notes") or ""),
            str(candidate.get("evidence_snippet") or ""),
        ]).lower()
        return any([
            candidate.get("drug_tier") in {"preferred", "non_preferred", "excluded"},
            candidate.get("prior_authorization"),
            candidate.get("step_therapy"),
            candidate.get("coverage_status") in {"restricted", "not_covered"},
            "preferred" in notes,
            "non-preferred" in notes,
            "restricted" in notes,
            "unproven" in notes,
            "medically necessary" in notes,
        ])

    def _build_scope_aliases(self, metadata: Dict[str, object], document: PolicyDocument) -> set[str]:
        aliases: set[str] = set()

        primary = self._canonicalize_name(str(metadata.get("primary_drug") or ""))
        if primary:
            aliases.add(primary)

        for item in metadata.get("governed_drugs") or []:
            if not isinstance(item, dict):
                continue
            drug_name = self._canonicalize_name(item.get("drug_name") or "")
            if drug_name:
                aliases.add(drug_name)
            for brand in item.get("brand_names") or []:
                alias = self._canonicalize_name(brand)
                if alias:
                    aliases.add(alias)
                aliases.add(self._canonicalize_name(brand, preserve_brand=True))

        title_lower = document.title.lower()
        title_hints = {
            "rituximab": ["rituximab", "rituxan", "riabni", "ruxience", "truxima"],
            "bevacizumab": ["bevacizumab", "avastin", "mvasi", "zirabev", "alymsys", "avzivi", "jobevne", "vegzelma"],
            "trastuzumab": ["trastuzumab", "herceptin", "hercessi", "herzuma", "kanjinti", "ogivri", "ontruzant", "trazimera", "herceptin hylecta"],
            "botulinum toxins": ["botox", "dysport", "daxxify", "xeomin", "myobloc", "botulinum toxins"],
        }
        for key, names in title_hints.items():
            if key in title_lower or any(name in title_lower for name in names):
                aliases.update(self._canonicalize_name(name) for name in names)
                aliases.update(self._canonicalize_name(name, preserve_brand=True) for name in names)

        return {alias for alias in aliases if alias}

    def _build_family_alias_groups(self, metadata: Dict[str, object], document: PolicyDocument) -> List[tuple[str, set[str]]]:
        groups: List[tuple[str, set[str]]] = []
        for item in metadata.get("governed_drugs") or []:
            if not isinstance(item, dict):
                continue
            aliases = set()
            drug_name = self._canonicalize_name(item.get("drug_name") or "")
            if drug_name:
                aliases.add(drug_name)
            for brand in item.get("brand_names") or []:
                alias = self._canonicalize_name(brand)
                if alias:
                    aliases.add(alias)
            if aliases:
                groups.append((drug_name or next(iter(aliases)), aliases))

        if groups:
            return groups

        scope_aliases = self._build_scope_aliases(metadata, document)
        if not scope_aliases:
            return []
        return [(metadata.get("primary_drug") or "policy_scope", scope_aliases)]

    def _has_structured_value(self, candidate: dict) -> bool:
        return any([
            candidate.get("hcpcs_code"),
            candidate.get("coverage_status") and candidate.get("coverage_status") != "unknown",
            candidate.get("prior_auth_criteria"),
            candidate.get("covered_indications"),
            candidate.get("step_therapy_requirements"),
            candidate.get("drug_tier") and candidate.get("drug_tier") != "not_applicable",
        ])

    def _candidate_to_payload(self, candidate: dict) -> dict:
        notes = candidate.get("notes")
        evidence = candidate.get("evidence_snippet")
        if evidence and evidence.lower() not in (notes or "").lower():
            notes = "{0} Evidence: {1}".format((notes or "").strip(), evidence).strip()

        return {
            "drug_name": candidate.get("drug_name"),
            "generic_name": candidate.get("generic_name") or candidate.get("drug_name"),
            "family_name": candidate.get("family_name") or candidate.get("generic_name") or candidate.get("drug_name"),
            "product_name": candidate.get("product_name"),
            "product_key": candidate.get("product_key"),
            "policy_name": candidate.get("policy_name"),
            "document_type": candidate.get("document_type"),
            "brand_names": candidate.get("brand_names") or [],
            "hcpcs_code": candidate.get("hcpcs_code"),
            "drug_tier": candidate.get("drug_tier"),
            "covered_indications": candidate.get("covered_indications") or [],
            "prior_authorization": bool(candidate.get("prior_authorization")),
            "prior_auth_criteria": candidate.get("prior_auth_criteria") or [],
            "quantity_limit": bool(candidate.get("quantity_limit")),
            "quantity_limit_detail": candidate.get("quantity_limit_detail"),
            "step_therapy": bool(candidate.get("step_therapy")),
            "step_therapy_requirements": candidate.get("step_therapy_requirements") or [],
            "site_of_care": candidate.get("site_of_care") or [],
            "prescriber_requirements": candidate.get("prescriber_requirements"),
            "coverage_status": candidate.get("coverage_status"),
            "coverage_bucket": candidate.get("coverage_bucket") or self._derive_coverage_bucket(candidate),
            "source_pages": candidate.get("source_pages") or [],
            "source_section": candidate.get("source_section"),
            "evidence_snippet": candidate.get("evidence_snippet"),
            "notes": notes or None,
            "confidence_score": float(candidate.get("confidence_score") or 0.5),
        }

    # -------------------------------------------------------------------------
    # Regex / heuristics
    # -------------------------------------------------------------------------

    def _detect_payer_heuristic(self, document: PolicyDocument) -> Optional[str]:
        seed = "{0}\n{1}".format(document.title, first_pages_text(document, count=2, max_chars=5000))
        return self._detect_payer_heuristic_from_text(seed)

    def _detect_payer_heuristic_from_text(self, raw_text: str) -> Optional[str]:
        lower = raw_text.lower()
        if "blue cross nc" in lower or "blue cross blue shield association" in lower:
            return "Blue Cross NC"
        if "unitedhealthcare" in lower or "united healthcare" in lower:
            return "UnitedHealthcare"
        if "cigna" in lower:
            return "Cigna"
        if "florida blue" in lower:
            return "Florida Blue"
        if "emblemhealth" in lower or "prime therapeutics" in lower:
            return "EmblemHealth"
        return None

    def _extract_policy_number(self, raw_text: str) -> Optional[str]:
        patterns = [
            r"policy number[:\s]+([A-Z0-9-]+)",
            r"coverage policy number[:\s]+([A-Z0-9-]+)",
            r"\b(IP\d{4})\b",
            r"\b(\d{4}D\d{4,}[A-Z]{0,3})\b",
        ]
        for pattern in patterns:
            match = re.search(pattern, raw_text[:8000], flags=re.IGNORECASE)
            if match:
                return match.group(1).strip().upper()
        return None

    def _extract_date(self, raw_text: str, labels: List[str]) -> Optional[str]:
        sample = raw_text[:8000]
        for label in labels:
            pattern = r"{0}[^0-9A-Za-z]*(\d{{1,2}}/\d{{1,2}}/\d{{4}}|[A-Za-z]+ \d{{1,2}}, \d{{4}}|[A-Za-z]+ \d{{4}})".format(
                re.escape(label)
            )
            match = re.search(pattern, sample, flags=re.IGNORECASE)
            if match:
                return self._normalize_date(match.group(1))
        return None

    def _normalize_date(self, value: str) -> Optional[str]:
        cleaned = value.strip()
        mmddyyyy = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", cleaned)
        if mmddyyyy:
            month, day, year = mmddyyyy.groups()
            return "{0}-{1:0>2}-{2:0>2}".format(year, month, day)

        month_year = re.fullmatch(r"([A-Za-z]+)\s+(\d{4})", cleaned)
        if month_year:
            month_name, year = month_year.groups()
            month = self._month_number(month_name)
            if month:
                return "{0}-{1}-01".format(year, month)

        month_day_year = re.fullmatch(r"([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})", cleaned)
        if month_day_year:
            month_name, day, year = month_day_year.groups()
            month = self._month_number(month_name)
            if month:
                return "{0}-{1}-{2:0>2}".format(year, month, day)

        return None

    def _month_number(self, month_name: str) -> Optional[str]:
        months = {
            "january": "01",
            "february": "02",
            "march": "03",
            "april": "04",
            "may": "05",
            "june": "06",
            "july": "07",
            "august": "08",
            "september": "09",
            "october": "10",
            "november": "11",
            "december": "12",
        }
        return months.get(month_name.strip().lower())

    def _extract_jcodes_from_text(self, raw_text: str) -> Dict[str, str]:
        jcode_pattern = re.compile(r"\b(J\d{4}|Q\d{4})\b", re.IGNORECASE)
        jcode_map: Dict[str, str] = {}
        stop = {
            "with", "code", "hcpcs", "drug", "coverage", "medical", "plan",
            "benefit", "policy", "prior", "auth", "preferred", "requires",
        }
        for match in jcode_pattern.finditer(raw_text):
            code = match.group(1).upper()
            start = max(0, match.start() - 120)
            end = min(len(raw_text), match.end() + 120)
            context = raw_text[start:end].lower()
            tokens = re.findall(r"[a-z]{4,}", context)
            for token in tokens:
                if token not in stop and token not in jcode_map:
                    jcode_map[token] = code
        return jcode_map

    def _infer_governed_drugs_heuristic(self, document: PolicyDocument) -> List[dict]:
        text = first_pages_text(document, count=3, max_chars=12000)
        lines = [line.strip() for line in text.splitlines() if line.strip()]

        groups = {
            "bevacizumab": set(),
            "rituximab": set(),
            "trastuzumab": set(),
            "botulinum toxins": set(),
        }
        known_brands = {
            "bevacizumab": {"avastin", "mvasi", "zirabev", "alymsys", "avzivi", "jobevne", "vegzelma"},
            "rituximab": {"rituxan", "riabni", "ruxience", "truxima", "rituxan hycela"},
            "trastuzumab": {"herceptin", "herceptin hylecta", "hercessi", "herzuma", "kanjinti", "ogivri", "ontruzant", "trazimera"},
            "botulinum toxins": {"dysport", "daxxify", "xeomin", "botox", "myobloc"},
        }

        for line in lines:
            lowered = line.lower()
            for family, brands in known_brands.items():
                if family in lowered:
                    groups[family].update(brands)
                for brand in brands:
                    if brand in lowered:
                        groups[family].add(brand)

        results = []
        for family, brands in groups.items():
            if not brands and family not in text.lower():
                continue
            results.append({
                "drug_name": family,
                "brand_names": sorted(brands),
            })
        return results

    def _refine_governed_drugs(self, document: PolicyDocument, metadata: Dict[str, object]) -> List[dict]:
        governed = metadata.get("governed_drugs") or []
        normalized = []
        seen = set()

        for item in governed:
            if not isinstance(item, dict):
                continue
            drug_name = self._canonicalize_name(item.get("drug_name") or "")
            brand_names = [
                brand.strip()
                for brand in (item.get("brand_names") or [])
                if isinstance(brand, str) and brand.strip()
            ]
            key = (drug_name, tuple(sorted(brand_names)))
            if not drug_name or key in seen:
                continue
            seen.add(key)
            normalized.append({
                "drug_name": drug_name,
                "brand_names": list(dict.fromkeys(brand_names)),
            })

        if normalized:
            return normalized

        primary = self._canonicalize_name(str(metadata.get("primary_drug") or ""))
        if primary:
            return [{"drug_name": primary, "brand_names": []}]
        return self._infer_governed_drugs_heuristic(document)

    def _metadata_is_sufficient(self, metadata: Dict[str, object]) -> bool:
        payer = metadata.get("payer")
        document_type = metadata.get("document_type")
        policy_number = metadata.get("policy_number")
        primary_drug = metadata.get("primary_drug")
        policy_scope = metadata.get("policy_scope")
        return bool(
            payer
            and payer != "Unknown"
            and document_type
            and document_type != "unknown"
            and (policy_number or primary_drug or policy_scope)
        )

    # -------------------------------------------------------------------------
    # Low-level normalizers
    # -------------------------------------------------------------------------

    def _coverage_key(self, candidate: dict, document_type: str = "drug_policy") -> str:
        if document_type == "program_policy" and candidate.get("product_key"):
            return str(candidate.get("product_key"))
        names = [candidate.get("drug_name")] + list(candidate.get("brand_names") or [])
        normalized = [self._canonicalize_name(name or "") for name in names]
        normalized = [name for name in normalized if name]
        return min(normalized) if normalized else ""

    def _canonicalize_name(self, value: str, preserve_brand: bool = False) -> str:
        cleaned = re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()
        if preserve_brand:
            return cleaned
        alias_map = {
            "rituxan": "rituximab",
            "riabni": "rituximab",
            "ruxience": "rituximab",
            "truxima": "rituximab",
            "rituxan hycela": "rituximab",
            "avastin": "bevacizumab",
            "mvasi": "bevacizumab",
            "zirabev": "bevacizumab",
            "alymsys": "bevacizumab",
            "avzivi": "bevacizumab",
            "jobevne": "bevacizumab",
            "vegzelma": "bevacizumab",
            "herceptin": "trastuzumab",
            "herceptin hylecta": "trastuzumab",
            "hercessi": "trastuzumab",
            "herzuma": "trastuzumab",
            "kanjinti": "trastuzumab",
            "ogivri": "trastuzumab",
            "ontruzant": "trastuzumab",
            "trazimera": "trastuzumab",
            "botox": "botox",
            "dysport": "dysport",
            "daxxify": "daxxify",
            "xeomin": "xeomin",
            "myobloc": "myobloc",
            "botulinum toxins": "botulinum toxins",
        }
        return alias_map.get(cleaned, cleaned)

    def _derive_product_key(self, brand_names: List[str], document_type: str) -> Optional[str]:
        if document_type != "program_policy":
            return None
        if brand_names:
            return self._canonicalize_name(brand_names[0], preserve_brand=True)
        return None

    def _infer_chunk_drug(
        self,
        content: str,
        metadata: Dict[str, object],
        primary_drug: Optional[str],
    ) -> tuple[Optional[str], Optional[str]]:
        lowered = content.lower()
        for item in metadata.get("governed_drugs") or []:
            if not isinstance(item, dict):
                continue
            family = self._canonicalize_name(item.get("drug_name") or "")
            aliases = [family]
            aliases.extend(self._canonicalize_name(brand, preserve_brand=True) for brand in (item.get("brand_names") or []))
            aliases.extend(self._canonicalize_name(brand) for brand in (item.get("brand_names") or []))
            for alias in aliases:
                if alias and alias in lowered:
                    return family or primary_drug, alias
        return primary_drug, None

    def _derive_coverage_bucket(self, candidate: dict) -> str:
        coverage_status = self._normalize_coverage_status(candidate)
        if coverage_status == "not_covered":
            return "not_covered"
        if candidate.get("step_therapy"):
            return "step_therapy"
        if candidate.get("prior_authorization"):
            return "pa_required"
        if coverage_status == "restricted":
            return "restricted"
        return "covered"

    def _tokenize_name(self, value: str) -> List[str]:
        return [piece for piece in re.findall(r"[a-z0-9]+", value.lower()) if len(piece) >= 4]

    def _normalize_string_list(self, values: object) -> List[str]:
        if not isinstance(values, list):
            return []
        cleaned = []
        for value in values:
            if isinstance(value, str):
                text = value.strip()
                if text:
                    cleaned.append(text)
        return list(dict.fromkeys(cleaned))

    def _normalize_optional_string(self, value: object) -> Optional[str]:
        if not isinstance(value, str):
            return None
        cleaned = value.strip()
        return cleaned or None

    def _normalize_jcode(self, value: object) -> Optional[str]:
        if not isinstance(value, str):
            return None
        match = re.search(r"\b([CJQ]\d{4})\b", value.upper())
        return match.group(1) if match else None

    def _normalize_site_of_care(self, values: List[str]) -> List[str]:
        normalized = []
        for value in values:
            lowered = str(value).strip().lower()
            if lowered in {"hospital", "hospital outpatient", "outpatient hospital"}:
                normalized.append("hospital")
            elif lowered in {"office", "physician office", "clinic"}:
                normalized.append("office")
            elif lowered in {"home", "home infusion"}:
                normalized.append("home")
        return list(dict.fromkeys(normalized))

    def _normalize_coverage_status(self, candidate: dict) -> str:
        status = str(candidate.get("coverage_status") or "").strip().lower()
        notes = " ".join([
            str(candidate.get("notes") or ""),
            " ".join(candidate.get("prior_auth_criteria") or []),
            " ".join(candidate.get("step_therapy_requirements") or []),
            str(candidate.get("evidence_snippet") or ""),
        ]).lower()

        if status in {"covered", "not_covered", "restricted", "unknown"}:
            return status
        if "not medically necessary" in notes or "excluded" in notes or "not covered" in notes:
            return "not_covered"
        if "unproven" in notes:
            return "not_covered"
        if candidate.get("prior_authorization") or candidate.get("step_therapy") or candidate.get("drug_tier") in {"preferred", "non_preferred", "excluded"}:
            return "restricted"
        return "covered" if candidate.get("covered_indications") else "unknown"

    def _dominant_section(self, values: List[str]) -> str:
        counts: Dict[str, int] = {}
        for value in values:
            counts[value] = counts.get(value, 0) + 1
        return max(counts, key=counts.get) if counts else "general"

    def _find_relevant_pages_for_product(self, document: PolicyDocument, brand: str, generic_name: str) -> List[int]:
        aliases = [brand.lower(), generic_name.lower() if generic_name else ""]
        pages = []
        for page in document.pages:
            text = page.text.lower()
            if any(alias and alias in text for alias in aliases):
                pages.append(page.page_number)
        return pages[:4] or [1]

    def _find_code_for_product(self, brand: str, generic_name: str, text: str, jcode_map: Dict[str, str]) -> Optional[str]:
        aliases = [brand.lower(), generic_name.lower() if generic_name else ""]
        for alias in aliases:
            if not alias:
                continue
            forward = re.search(r"{0}.{{0,140}}\b([JQ]\d{{4}})\b".format(re.escape(alias)), text, flags=re.IGNORECASE | re.DOTALL)
            if forward:
                return forward.group(1).upper()
            backward = re.search(r"\b([JQ]\d{{4}})\b.{{0,140}}{0}".format(re.escape(alias)), text, flags=re.IGNORECASE | re.DOTALL)
            if backward:
                return backward.group(1).upper()
            for token in self._tokenize_name(alias):
                if token in jcode_map:
                    return jcode_map[token]
        return None

    def _infer_program_tier(self, brand: str, generic_name: str, pages: List[object]) -> Optional[str]:
        alias = brand.lower()
        generic = generic_name.lower() if generic_name else ""
        for page in pages:
            text = page.text.lower()
            if alias not in text and generic not in text:
                continue
            if "non-preferred" in text and alias in text and "preferred " not in text.split(alias)[0][-80:]:
                return "non_preferred"
            if "preferred" in text and alias in text and "non-preferred" not in text.split(alias)[0][-80:]:
                return "preferred"
            if "restricted product" in text and alias in text:
                return "non_preferred"
            if "excluded from coverage" in text and alias in text:
                return "excluded"
        return "not_applicable"

    def _infer_program_notes(self, brand: str, generic_name: str, pages: List[object]) -> Optional[str]:
        alias = brand.lower()
        generic = generic_name.lower() if generic_name else ""
        snippets = []
        for page in pages:
            text = page.text.lower()
            if alias not in text and generic not in text:
                continue
            if "subcutaneous" in text and alias in text:
                snippets.append("Subcutaneous administration noted.")
            if "intravenous" in text and alias in text:
                snippets.append("Intravenous administration noted.")
            if "iv" in text and "first" in text and alias in text:
                snippets.append("IV-first requirement noted.")
            if "preferred" in text and alias in text:
                snippets.append("Preferred/non-preferred program placement noted.")
            if "medically necessary" in text:
                snippets.append("Medical necessity criteria present.")
        return " ".join(dict.fromkeys(snippets)) if snippets else None

    def _extract_evidence_snippet(self, brand: str, generic_name: str, pages: List[object]) -> Optional[str]:
        alias = brand.lower()
        generic = generic_name.lower() if generic_name else ""
        for page in pages:
            for line in page.text.splitlines():
                lowered = line.lower()
                if alias in lowered or (generic and generic in lowered):
                    return line.strip()[:220]
        return None

    def _apply_program_policy_overrides(self, document: PolicyDocument, row: dict) -> None:
        title = document.title.lower()
        product_key = str(row.get("product_key") or "")

        if "preferred injectable oncology program" in title:
            preferred = {
                "mvasi",
                "zirabev",
                "riabni",
                "ruxience",
                "truxima",
                "ogivri",
                "ontruzant",
                "trazimera",
                "kanjinti",
            }
            row["drug_tier"] = "preferred" if product_key in preferred else "non_preferred"
            row["coverage_status"] = "restricted"
            row["prior_authorization"] = True

            if product_key == "rituxan hycela":
                row["notes"] = self._append_note(row.get("notes"), "Subcutaneous formulation with IV-first requirement noted.")
            if product_key == "herceptin hylecta":
                row["notes"] = self._append_note(row.get("notes"), "Subcutaneous formulation noted.")

        if "botulinum toxins a and b" in title:
            if product_key == "daxxify":
                row["drug_tier"] = "excluded"
                row["coverage_status"] = "not_covered"
                row["notes"] = self._append_note(row.get("notes"), "Typically excluded from coverage.")
            else:
                row["drug_tier"] = "not_applicable"
                row["coverage_status"] = "restricted"
                row["prior_authorization"] = True
            if product_key == "myobloc":
                row["step_therapy"] = True
                row["notes"] = self._append_note(row.get("notes"), "Failure of preferred toxin products required for some uses.")

    def _append_note(self, existing: Optional[str], addition: str) -> str:
        parts = [part for part in [existing, addition] if part]
        return " ".join(dict.fromkeys(parts))

    # -------------------------------------------------------------------------
    # JSON helpers
    # -------------------------------------------------------------------------

    def _extract_response_text(self, payload: Dict) -> str:
        candidates = payload.get("candidates", [])
        if not candidates:
            raise ValueError("Gemini returned no candidates.")
        first = candidates[0]
        content = first.get("content", {}) if isinstance(first, dict) else {}
        parts = content.get("parts", []) if isinstance(content, dict) else []
        texts = [part["text"] for part in parts if isinstance(part, dict) and part.get("text")]
        text = "".join(texts).strip()
        if not text:
            raise ValueError("Gemini returned empty response.")
        if text.startswith("```"):
            text = text.strip("`").replace("json", "", 1).strip()
        return text

    def _parse_json_payload(self, text: str) -> Dict:
        cleaned = text.strip()
        for _ in range(3):
            try:
                parsed = json.loads(cleaned)
            except json.JSONDecodeError:
                break
            if isinstance(parsed, dict):
                return parsed
            if isinstance(parsed, str):
                cleaned = parsed.strip()
                continue
            raise ValueError("Unexpected type: {0}".format(type(parsed)))

        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start != -1 and end > start:
            try:
                parsed = json.loads(cleaned[start:end + 1])
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                pass

        return {"coverages": []}

    # -------------------------------------------------------------------------
    # Legacy QA helper
    # -------------------------------------------------------------------------

    def ask_question(self, question: str, context_rows: list) -> dict:
        return self.ask_question_rag(question, [
            {
                "payer": row.get("payer", "Unknown"),
                "drug_name": row.get("drug_name", ""),
                "section_type": "general",
                "content": self._row_to_text(row),
            }
            for row in context_rows[:20]
        ])

    def _row_to_text(self, row: dict) -> str:
        parts = []
        if row.get("coverage_status"):
            parts.append("Coverage: " + row["coverage_status"])
        if row.get("prior_authorization"):
            criteria = "; ".join(row.get("prior_auth_criteria") or [])
            parts.append("Prior auth required" + (": " + criteria if criteria else ""))
        if row.get("step_therapy"):
            reqs = "; ".join(row.get("step_therapy_requirements") or [])
            parts.append("Step therapy required" + (": " + reqs if reqs else ""))
        if row.get("covered_indications"):
            parts.append("Indications: " + "; ".join(row["covered_indications"]))
        if row.get("site_of_care"):
            parts.append("Site of care: " + ", ".join(row["site_of_care"]))
        return " | ".join(parts)
