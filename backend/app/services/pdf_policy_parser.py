from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import re
from typing import Dict, Iterable, List, Optional

import pdfplumber


LOW_VALUE_SECTIONS = {"references", "revision_history", "instructions", "evidence_summary"}
PROGRAM_HIGH_VALUE_SECTIONS = {"coverage", "coding"}
BCBS_PROGRAM_BRANDS = {
    "bevacizumab": ["Mvasi", "Zirabev", "Avastin", "Alymsys", "Avzivi", "Jobevne", "Vegzelma"],
    "rituximab": ["Riabni", "Ruxience", "Truxima", "Rituxan", "Rituxan Hycela"],
    "trastuzumab": ["Ogivri", "Ontruzant", "Trazimera", "Herceptin", "Hercessi", "Herzuma", "Kanjinti", "Herceptin Hylecta"],
}
BCBS_PREFERRED_PRODUCTS = {
    "mvasi",
    "zirabev",
    "riabni",
    "ruxience",
    "truxima",
    "ogivri",
    "ontruzant",
    "trazimera",
}
BCBS_NON_PREFERRED_PRODUCTS = {
    "avastin",
    "alymsys",
    "avzivi",
    "jobevne",
    "vegzelma",
    "rituxan",
    "rituxan hycela",
    "herceptin",
    "hercessi",
    "herzuma",
    "kanjinti",
    "herceptin hylecta",
}
BCBS_HISTORY_CODE_MAP = {
    "riabni": "Q5123",
    "ogivri": "Q5114",
    "ontruzant": "Q5112",
    "trazimera": "Q5116",
}
UHC_PRODUCTS = {
    "Dysport": "dysport",
    "Daxxify": "daxxify",
    "Xeomin": "xeomin",
    "Botox": "botox",
    "Myobloc": "myobloc",
}


@dataclass(frozen=True)
class PolicyPage:
    page_number: int
    text: str
    section_type: str
    heading: Optional[str] = None


@dataclass(frozen=True)
class PolicyDocument:
    title: str
    source_name: str
    document_type: str
    pages: List[PolicyPage]
    raw_text: str


def parse_pdf_bytes(file_bytes: bytes, source_name: str = "uploaded.pdf") -> PolicyDocument:
    with pdfplumber.open(BytesIO(file_bytes)) as pdf:
        return _build_policy_document(pdf.pages, source_name)


def parse_pdf_path(path: str) -> PolicyDocument:
    with pdfplumber.open(path) as pdf:
        return _build_policy_document(pdf.pages, path.rsplit("/", 1)[-1])


def build_rag_chunks(document: PolicyDocument, max_chars: int = 5500) -> List[dict]:
    chunks: List[dict] = []
    current_pages: List[int] = []
    current_parts: List[str] = []
    current_sections: List[str] = []
    chunk_index = 0

    for page in document.pages:
        if not page.text.strip():
            continue

        page_block = "Page {0}\n{1}".format(page.page_number, page.text.strip())
        projected_size = sum(len(part) for part in current_parts) + len(page_block)

        if current_parts and projected_size > max_chars:
            chunks.append(_make_chunk(chunk_index, current_pages, current_parts, current_sections))
            chunk_index += 1
            current_pages = []
            current_parts = []
            current_sections = []

        current_pages.append(page.page_number)
        current_parts.append(page_block)
        current_sections.append(page.section_type)

    if current_parts:
        chunks.append(_make_chunk(chunk_index, current_pages, current_parts, current_sections))

    return chunks


def build_extraction_chunks(document: PolicyDocument, max_chars: int = 6500) -> List[dict]:
    relevant_pages = [page for page in document.pages if page.section_type not in LOW_VALUE_SECTIONS and page.text.strip()]
    if not relevant_pages:
        relevant_pages = [page for page in document.pages if page.text.strip()]

    chunks: List[dict] = []
    current_pages: List[int] = []
    current_parts: List[str] = []
    current_sections: List[str] = []
    chunk_index = 0

    for page in relevant_pages:
        page_block = "Page {0} | Section: {1}\n{2}".format(
            page.page_number,
            page.section_type,
            page.text.strip(),
        )
        projected_size = sum(len(part) for part in current_parts) + len(page_block)

        if current_parts and projected_size > max_chars:
            chunks.append(_make_chunk(chunk_index, current_pages, current_parts, current_sections))
            chunk_index += 1
            current_pages = []
            current_parts = []
            current_sections = []

        current_pages.append(page.page_number)
        current_parts.append(page_block)
        current_sections.append(page.section_type)

    if current_parts:
        chunks.append(_make_chunk(chunk_index, current_pages, current_parts, current_sections))

    return chunks


def build_program_extraction_chunks(document: PolicyDocument, max_chars: int = 4200) -> List[dict]:
    pages = [page for page in document.pages if page.text.strip()]
    if not pages:
        return []

    prioritized = [
        page for page in pages
        if page.section_type in PROGRAM_HIGH_VALUE_SECTIONS
        or _contains_program_signal(page.text)
    ]
    secondary = [
        page for page in pages
        if page not in prioritized and page.section_type not in LOW_VALUE_SECTIONS
    ]
    chosen_pages = prioritized + secondary[:4]

    chunks: List[dict] = []
    chunk_index = 0
    current_pages: List[int] = []
    current_parts: List[str] = []
    current_sections: List[str] = []

    for page in chosen_pages:
        block = "Page {0} | Section: {1}\n{2}".format(
            page.page_number,
            page.section_type,
            page.text.strip(),
        )
        projected = sum(len(part) for part in current_parts) + len(block)
        should_break = (
            current_parts
            and (
                projected > max_chars
                or page.section_type in PROGRAM_HIGH_VALUE_SECTIONS
                or _contains_program_signal(page.text)
            )
        )
        if should_break:
            chunks.append(_make_chunk(chunk_index, current_pages, current_parts, current_sections))
            chunk_index += 1
            current_pages = []
            current_parts = []
            current_sections = []

        current_pages.append(page.page_number)
        current_parts.append(block)
        current_sections.append(page.section_type)

    if current_parts:
        chunks.append(_make_chunk(chunk_index, current_pages, current_parts, current_sections))

    return chunks


def first_pages_text(document: PolicyDocument, count: int = 3, max_chars: int = 12000) -> str:
    parts = []
    used = 0
    for page in document.pages[:count]:
        if not page.text.strip():
            continue
        block = "Page {0}\n{1}".format(page.page_number, page.text.strip())
        remaining = max_chars - used
        if remaining <= 0:
            break
        parts.append(block[:remaining])
        used += min(len(block), remaining)
    return "\n\n".join(parts)


def infer_primary_drug_hint(document: PolicyDocument) -> Optional[str]:
    title = document.title.lower()
    if "rituximab" in title:
        return "rituximab"
    if "bevacizumab" in title or "avastin" in title:
        return "bevacizumab"
    if "trastuzumab" in title:
        return "trastuzumab"
    if "botulinum toxins" in title:
        return "botulinum toxins"
    return None


def extract_program_policy_backbone(document: PolicyDocument) -> Optional[dict]:
    title = document.title.lower()
    if "preferred injectable oncology program" in title:
        return _extract_bcbs_program_backbone(document)
    if "botulinum toxins a and b" in title:
        return _extract_uhc_program_backbone(document)
    return None


def _build_policy_document(pdf_pages: Iterable, source_name: str) -> PolicyDocument:
    pages: List[PolicyPage] = []
    raw_parts: List[str] = []

    for page_number, page in enumerate(pdf_pages, 1):
        text = (page.extract_text() or "").strip()
        if not text:
            continue
        section_type = _infer_section_type(text)
        heading = _extract_heading(text)
        pages.append(PolicyPage(
            page_number=page_number,
            text=text,
            section_type=section_type,
            heading=heading,
        ))
        raw_parts.append(text)

    if not pages:
        raise ValueError("No readable text found in the PDF.")

    title = _extract_title(pages)
    document_type = _infer_document_type(title, pages)
    return PolicyDocument(
        title=title,
        source_name=source_name,
        document_type=document_type,
        pages=pages,
        raw_text="\n\n".join(raw_parts),
    )


def _make_chunk(chunk_index: int, page_numbers: List[int], parts: List[str], section_types: List[str]) -> dict:
    dominant_section = _dominant_value(section_types)
    return {
        "chunk_index": chunk_index,
        "content": "\n\n".join(parts).strip(),
        "page_number": page_numbers[0],
        "page_numbers": list(page_numbers),
        "section_type": dominant_section or "general",
    }


def _dominant_value(values: List[str]) -> Optional[str]:
    counts = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    if not counts:
        return None
    return max(counts, key=counts.get)


def _extract_title(pages: List[PolicyPage]) -> str:
    first_lines = []
    for page in pages[:2]:
        first_lines.extend([line.strip() for line in page.text.splitlines()[:15] if line.strip()])

    for idx, line in enumerate(first_lines):
        if line.lower().startswith("corporate medical policy:"):
            cleaned = line.split(":", 1)[1].strip()
            if cleaned:
                return cleaned

        if line.lower().startswith("policy title"):
            cleaned = re.sub(r"^.*policy title[.:\s]*", "", line, flags=re.IGNORECASE)
            cleaned = cleaned.strip(" .:-…")
            if cleaned and len(cleaned) >= 20:
                return cleaned
            if idx + 1 < len(first_lines):
                next_line = first_lines[idx + 1].strip(" .:-…")
                if next_line:
                    combined = "{0} {1}".format(cleaned, next_line).strip() if cleaned else next_line
                    if len(combined) >= len(cleaned):
                        return combined

    for idx, line in enumerate(first_lines):
        lowered = line.lower()
        if "medical benefit drug policy" in lowered and idx + 1 < len(first_lines):
            next_line = first_lines[idx + 1].strip(" .:-")
            if next_line and "policy number" not in next_line.lower():
                return next_line

    candidates = [
        line for line in first_lines
        if len(line) >= 12
        and len(line) <= 120
        and any(token in line.lower() for token in ["policy", "program", "drug", "toxins", "products", "oncology"])
        and "page " not in line.lower()
        and "copyright" not in line.lower()
    ]
    if candidates:
        prioritized = sorted(
            candidates,
            key=lambda line: (
                "policy number" in line.lower(),
                "effective date" in line.lower(),
                -len(line),
            ),
        )
        top = prioritized[0]
        top_index = first_lines.index(top) if top in first_lines else -1
        if 0 <= top_index < len(first_lines) - 1:
            next_line = first_lines[top_index + 1].strip(" .:-…")
            if next_line and (
                next_line[:1].islower()
                or "products for" in next_line.lower()
                or "indications" in next_line.lower()
            ):
                return "{0} {1}".format(top, next_line).strip()
        return top
    return first_lines[0] if first_lines else "Untitled Policy"


def _extract_heading(text: str) -> Optional[str]:
    for line in text.splitlines()[:8]:
        cleaned = line.strip()
        if 3 <= len(cleaned) <= 100:
            return cleaned
    return None


def _infer_document_type(title: str, pages: List[PolicyPage]) -> str:
    seed = "{0}\n{1}".format(title, "\n".join(page.text[:1500] for page in pages[:2])).lower()
    if any(term in seed for term in ["medical drug list", "formulary", "preferred specialty"]):
        return "formulary_list"
    if any(term in seed for term in ["preferred injectable oncology program", "botulinum toxins a and b", "this policy refers to the following"]):
        return "program_policy"
    if any(term in seed for term in ["coverage policy number", "products for non-oncology indications", "drug coverage policy"]):
        return "drug_policy"
    if "program" in title.lower():
        return "program_policy"
    return "drug_policy"


def _infer_section_type(text: str) -> str:
    sample = text[:2500].lower()
    if any(term in sample for term in [
        "coverage rationale",
        "general requirements",
        "medical necessity",
        "coverage policy",
        "diagnosis-specific requirements",
        "restricted product",
        "preferred ",
        "non-preferred",
        "unproven",
        "proven in the treatment",
    ]):
        return "coverage"
    if any(term in sample for term in ["applicable codes", "hcpcs", "j-code", "icd-10", "coding"]):
        return "coding"
    if any(term in sample for term in ["fda approved use", "proven in the treatment", "indications", "overview"]):
        return "indications"
    if any(term in sample for term in ["policy history", "revision information", "revision history", "change history"]):
        return "revision_history"
    if any(term in sample for term in ["instructions for use", "table of contents"]):
        return "instructions"
    if any(term in sample for term in ["references", "bibliography", "peer reviewed", "clinical evidence", "study", "cochrane"]):
        return "references" if "references" in sample or "bibliography" in sample else "evidence_summary"
    return "general"


def _contains_program_signal(text: str) -> bool:
    sample = text[:2000].lower()
    return any(term in sample for term in [
        "restricted product",
        "preferred ",
        "non-preferred",
        "this policy refers to the following",
        "coverage rationale",
        "general requirements",
        "medically necessary",
        "unproven",
    ])


def _extract_bcbs_program_backbone(document: PolicyDocument) -> dict:
    approved_blocks = _collect_product_blocks(document, range(2, 8), _all_bcbs_brands())
    coding_blocks = _collect_product_blocks(document, range(10, 25), _all_bcbs_brands())
    family_rules = _extract_bcbs_family_rules(document)
    rows = []

    for family, brands in BCBS_PROGRAM_BRANDS.items():
        for brand in brands:
            key = _normalize_key(brand)
            approved_text = "\n".join(approved_blocks.get(key, []))
            coding_text = "\n".join(coding_blocks.get(key, []))
            block_text = "{0}\n{1}".format(approved_text, coding_text).strip()
            hcpcs_codes = _extract_codes_for_product(coding_text)
            max_units = _extract_max_units(coding_text)
            notes = _extract_bcbs_notes(coding_text)
            route = _extract_route_notes(coding_text)
            if route:
                notes.append(route)
            if key == "rituxan hycela":
                notes.append("Requires at least one full rituximab dose by IV infusion before subcutaneous use.")
            if key == "herceptin hylecta":
                notes.append("Subcutaneous formulation; do not administer intravenously.")

            tier = "preferred" if key in BCBS_PREFERRED_PRODUCTS else "non_preferred"
            quantity_limit = bool(max_units and tier == "non_preferred")
            quantity_limit_detail = "Maximum units: {0}".format(max_units) if quantity_limit else None
            family_rule = family_rules.get(family, {})
            is_non_preferred = key in BCBS_NON_PREFERRED_PRODUCTS

            row = {
                "drug_name": family,
                "brand_names": [brand],
                "policy_name": document.title,
                "product_key": key,
                "family_name": family,
                "drug_tier": tier,
                "coverage_status": "restricted" if is_non_preferred else "covered",
                "prior_authorization": True,
                "prior_auth_criteria": _build_bcbs_prior_auth_criteria(family_rule, is_non_preferred),
                "step_therapy": is_non_preferred,
                "step_therapy_requirements": _build_bcbs_step_requirements(family_rule, is_non_preferred),
                "quantity_limit": quantity_limit,
                "quantity_limit_detail": quantity_limit_detail,
                "hcpcs_code": hcpcs_codes[0] if hcpcs_codes else BCBS_HISTORY_CODE_MAP.get(key),
                "all_hcpcs_codes": hcpcs_codes,
                "covered_indications": _extract_bcbs_indications(approved_text or coding_text),
                "notes": " ".join(dict.fromkeys(note for note in notes if note)) or None,
                "source_pages": _find_pages_for_brand(document, brand),
                "source_section": "coverage" if approved_text else "coding",
                "evidence_snippet": _first_meaningful_line(block_text),
                "confidence_score": 0.94 if block_text else 0.82,
            }
            if row["all_hcpcs_codes"] and len(row["all_hcpcs_codes"]) > 1:
                extra_codes = ", ".join(row["all_hcpcs_codes"])
                row["notes"] = _join_notes(row.get("notes"), "Additional HCPCS codes listed: {0}.".format(extra_codes))
            if tier == "preferred" and not row["covered_indications"]:
                row["covered_indications"] = _extract_bcbs_representative_indications(approved_blocks, family)
            rows.append(row)

    return {
        "payer": "Blue Cross NC",
        "plan_name": document.title,
        "state": "NC",
        "effective_date": _extract_month_year_from_pages(document.pages[:2]),
        "document_type": "program_policy",
        "governed_drugs": [
            {"drug_name": family, "brand_names": brands}
            for family, brands in BCBS_PROGRAM_BRANDS.items()
        ],
        "products": rows,
        "family_rules": family_rules,
    }


def _extract_uhc_program_backbone(document: PolicyDocument) -> dict:
    page1 = _page_text(document, 1)
    page2 = _page_text(document, 2)
    page3 = _page_text(document, 3)
    page4 = _page_text(document, 4)
    product_blocks = _collect_uhc_proven_blocks(page2)
    myobloc_criteria = _extract_uhc_myobloc_criteria(page3)
    code_map = _extract_uhc_code_map(page4)
    rows = []

    for brand, generic in UHC_PRODUCTS.items():
        key = _normalize_key(brand)
        indications = product_blocks.get(key, [])
        notes = []
        if key == "daxxify":
            notes.append("Typically excluded from coverage.")
        if key == "myobloc":
            notes.append("Medical necessity criteria and failure requirements are defined for specific diagnoses.")
        row = {
            "drug_name": generic,
            "brand_names": [brand],
            "policy_name": document.title,
            "product_key": key,
            "family_name": "botulinum toxins",
            "drug_tier": "excluded" if key == "daxxify" else "not_applicable",
            "coverage_status": "not_covered" if key == "daxxify" else "restricted",
            "prior_authorization": key != "daxxify",
            "prior_auth_criteria": myobloc_criteria if key == "myobloc" else [
                "Coverage is contingent on the General Requirements and Diagnosis-Specific Requirements sections."
            ],
            "step_therapy": key == "myobloc",
            "step_therapy_requirements": _extract_uhc_step_requirements(myobloc_criteria) if key == "myobloc" else [],
            "quantity_limit": False,
            "quantity_limit_detail": None,
            "hcpcs_code": code_map.get(key),
            "all_hcpcs_codes": [code_map[key]] if key in code_map else [],
            "covered_indications": indications,
            "notes": " ".join(notes) or None,
            "source_pages": _find_pages_for_brand(document, brand),
            "source_section": "coverage",
            "evidence_snippet": _first_meaningful_line(page1 if key == "daxxify" else page2),
            "confidence_score": 0.95,
        }
        rows.append(row)

    return {
        "payer": "UnitedHealthcare",
        "plan_name": document.title,
        "state": None,
        "effective_date": _extract_explicit_date(page1),
        "policy_number": _extract_policy_number_from_text(page1),
        "document_type": "program_policy",
        "governed_drugs": [
            {"drug_name": "botulinum toxins", "brand_names": list(UHC_PRODUCTS.keys())},
        ],
        "products": rows,
        "family_rules": {"botulinum toxins": {"general_requirements": [
            "Coverage for Dysport, Xeomin, Botox, and Myobloc is contingent on the General Requirements and Diagnosis-Specific Requirements sections."
        ]}},
    }


def _all_bcbs_brands() -> List[str]:
    brands: List[str] = []
    for family_brands in BCBS_PROGRAM_BRANDS.values():
        brands.extend(family_brands)
    return brands


def _collect_product_blocks(document: PolicyDocument, page_numbers: range, brands: List[str]) -> Dict[str, List[str]]:
    targets = {_normalize_key(brand): brand for brand in brands}
    blocks: Dict[str, List[str]] = {key: [] for key in targets}
    current_key: Optional[str] = None

    for page in document.pages:
        if page.page_number not in page_numbers:
            continue
        for raw_line in page.text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            matched_key = _match_brand_heading(line, brands)
            if matched_key:
                current_key = _normalize_key(matched_key)
                blocks.setdefault(current_key, []).append(line)
                continue
            if current_key:
                blocks[current_key].append(line)
    return {key: value for key, value in blocks.items() if value}


def _collect_uhc_proven_blocks(page_text: str) -> Dict[str, List[str]]:
    lines = [line.strip() for line in page_text.splitlines() if line.strip()]
    blocks: Dict[str, List[str]] = {}
    current_key: Optional[str] = None

    for line in lines:
        matched_key = None
        for brand in UHC_PRODUCTS:
            if re.match(r"^{0}\b".format(re.escape(brand)), line, flags=re.IGNORECASE):
                matched_key = _normalize_key(brand)
                current_key = matched_key
                blocks.setdefault(current_key, [])
                break
        if matched_key:
            continue
        if current_key:
            if "Additional information to support medical necessity review" in line:
                current_key = None
                continue
            blocks[current_key].append(line)
    return {key: _extract_simple_indications(lines) for key, lines in blocks.items()}


def _match_brand_heading(line: str, brands: List[str]) -> Optional[str]:
    cleaned = line.replace("•", "").strip()
    for brand in sorted(brands, key=len, reverse=True):
        if re.match(r"^{0}(?:®|\b)".format(re.escape(brand)), cleaned, flags=re.IGNORECASE):
            return brand
    return None


def _extract_codes_for_product(text: str) -> List[str]:
    codes = re.findall(r"\b([CJQ]\d{4})\b", text, flags=re.IGNORECASE)
    return list(dict.fromkeys(code.upper() for code in codes))


def _extract_max_units(text: str) -> Optional[str]:
    match = re.search(r"\b(\d{4})\b", text)
    if match:
        return match.group(1)
    return None


def _extract_bcbs_indications(text: str) -> List[str]:
    indications: List[str] = []
    current: Optional[str] = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        normalized = line.replace("•", "").replace("o", "", 1).strip()
        if normalized.lower().startswith("for the treatment of"):
            current = normalized
            indications.append(current)
            continue
        if current and (
            line.startswith("▪")
            or line.startswith("o ")
            or line.lower().startswith("limitations of use")
            or "Page " in line
            or "January 2026" in line
            or re.search(r"\b([CJQ]\d{4})\b", line)
            or "intravenous" in line.lower()
            or "subcutaneous" in line.lower()
        ):
            if line.startswith("▪"):
                indications[-1] = "{0} {1}".format(indications[-1], line.lstrip("▪ ").strip())
            else:
                current = None
            continue
        if current and not re.match(r"^[A-Z][A-Za-z ]+®", line):
            indications[-1] = "{0} {1}".format(indications[-1], line.strip())
        else:
            current = None

    return _dedupe_lines(indications)


def _extract_simple_indications(lines: List[str]) -> List[str]:
    indications: List[str] = []
    current: Optional[str] = None

    for line in lines:
        if not line:
            continue
        if line.startswith("o "):
            if current:
                indications.append(current.strip())
            current = line.lstrip("o ").strip()
            continue
        if line.startswith(("▪", "and")):
            if current:
                current = "{0} {1}".format(current, line.lstrip("▪ ").strip())
            continue
        if re.match(r"^[A-Z]", line):
            if current:
                indications.append(current.strip())
                current = None
            indications.append(line.strip())
        elif current:
            current = "{0} {1}".format(current, line.strip())

    if current:
        indications.append(current.strip())
    return _dedupe_lines(indications)


def _extract_bcbs_family_rules(document: PolicyDocument) -> Dict[str, dict]:
    text = "\n".join(_page_text(document, page_no) for page_no in [8, 9, 10])
    rules = {
        "bevacizumab": {
            "step_therapy_requirement": "Documented serious adverse event requiring medical intervention to both preferred bevacizumab biosimilars Mvasi and Zirabev that is not anticipated with the requested product.",
            "preferred_products": ["Mvasi", "Zirabev"],
        },
        "rituximab": {
            "step_therapy_requirement": "Documented serious adverse event requiring medical intervention to all preferred rituximab biosimilars Riabni, Ruxience, and Truxima that is not anticipated with the requested product.",
            "preferred_products": ["Riabni", "Ruxience", "Truxima"],
        },
        "trastuzumab": {
            "step_therapy_requirement": "Documented serious adverse event requiring medical intervention to all preferred trastuzumab biosimilars Ogivri, Ontruzant, and Trazimera that is not anticipated with the requested product.",
            "preferred_products": ["Ogivri", "Ontruzant", "Trazimera"],
        },
    }
    if "medwatch" in text.lower():
        for rule in rules.values():
            rule["medwatch_requirement"] = "Prescriber completed and submitted an FDA MedWatch Adverse Event Reporting Form."
    return rules


def _build_bcbs_prior_auth_criteria(family_rule: dict, is_non_preferred: bool) -> List[str]:
    criteria = [
        "Requested dose and duration must be within FDA labeled or NCCN-supported dosing.",
    ]
    if is_non_preferred:
        if family_rule.get("step_therapy_requirement"):
            criteria.append(family_rule["step_therapy_requirement"])
        if family_rule.get("medwatch_requirement"):
            criteria.append(family_rule["medwatch_requirement"])
        criteria.append("Continuation requires continued clinical benefit with acceptable toxicity.")
    return criteria


def _build_bcbs_step_requirements(family_rule: dict, is_non_preferred: bool) -> List[str]:
    if not is_non_preferred:
        return []
    requirements = []
    if family_rule.get("step_therapy_requirement"):
        requirements.append(family_rule["step_therapy_requirement"])
    if family_rule.get("medwatch_requirement"):
        requirements.append(family_rule["medwatch_requirement"])
    return requirements


def _extract_bcbs_notes(text: str) -> List[str]:
    notes = []
    lowered = text.lower()
    if "do not administer iv" in lowered:
        notes.append("Subcutaneous formulation; do not administer intravenously.")
    if "receive at least one full dose" in lowered and "iv infusion" in lowered:
        notes.append("Requires at least one full rituximab dose by IV infusion before subcutaneous use.")
    if "non-specific assigned hcpcs codes" in lowered:
        notes.append("Policy lists non-specific assigned HCPCS codes and requires requested product NDC.")
    return notes


def _extract_route_notes(text: str) -> Optional[str]:
    lowered = text.lower()
    if "subcutaneous (sc)" in lowered:
        return "Subcutaneous administration."
    if "intravenous (iv)" in lowered:
        return "Intravenous administration."
    return None


def _extract_bcbs_representative_indications(approved_blocks: Dict[str, List[str]], family: str) -> List[str]:
    representative_order = {
        "bevacizumab": ["avastin", "jobevne", "alymsys", "vegzelma"],
        "rituximab": ["rituxan"],
        "trastuzumab": ["herceptin", "hercessi", "herzuma", "kanjinti"],
    }
    for key in representative_order.get(family, []):
        if key in approved_blocks:
            indications = _extract_bcbs_indications("\n".join(approved_blocks[key]))
            if indications:
                return indications
    return []


def _extract_uhc_myobloc_criteria(page_text: str) -> List[str]:
    criteria: List[str] = []
    current_label: Optional[str] = None
    buffer: List[str] = []

    for raw_line in page_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line in {"Cervical dystonia", "Detrusor overactivity (also known as detrusor hyperreflexia)", "Sialorrhea", "Spasticity"}:
            if current_label and buffer:
                criteria.append("{0}: {1}".format(current_label, " ".join(buffer)))
            current_label = line
            buffer = []
            continue
        if line.startswith("Unproven"):
            break
        if current_label:
            cleaned = line.lstrip("o▪ ").strip()
            if cleaned and "Additional information to support medical necessity review" not in cleaned:
                buffer.append(cleaned)

    if current_label and buffer:
        criteria.append("{0}: {1}".format(current_label, " ".join(buffer)))
    return _dedupe_lines(criteria)


def _extract_uhc_step_requirements(criteria: List[str]) -> List[str]:
    requirements = []
    for item in criteria:
        lowered = item.lower()
        if "history of failure" in lowered:
            requirements.append(item)
    return requirements


def _extract_uhc_code_map(page_text: str) -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    for line in page_text.splitlines():
        match = re.search(r"\b(J\d{4})\b.*\b(Botox|Dysport|Myobloc|Xeomin|Daxxify)\b", line, flags=re.IGNORECASE)
        if match:
            code, brand = match.groups()
            mapping[_normalize_key(brand)] = code.upper()
    return mapping


def _find_pages_for_brand(document: PolicyDocument, brand: str) -> List[int]:
    lowered = brand.lower()
    pages = [page.page_number for page in document.pages if lowered in page.text.lower()]
    return pages[:6] or [1]


def _page_text(document: PolicyDocument, page_number: int) -> str:
    for page in document.pages:
        if page.page_number == page_number:
            return page.text
    return ""


def _extract_explicit_date(text: str) -> Optional[str]:
    match = re.search(r"Effective Date:\s*([A-Za-z]+ \d{1,2}, \d{4})", text, flags=re.IGNORECASE)
    if not match:
        return None
    return _normalize_date_value(match.group(1))


def _extract_month_year_from_pages(pages: List[PolicyPage]) -> Optional[str]:
    text = "\n".join(page.text for page in pages)
    match = re.search(r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{4})\b", text)
    if not match:
        return None
    return _normalize_date_value("{0} {1}".format(match.group(1), match.group(2)))


def _normalize_date_value(value: str) -> Optional[str]:
    cleaned = value.strip()
    month_day_year = re.fullmatch(r"([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})", cleaned)
    if month_day_year:
        month_name, day, year = month_day_year.groups()
        month = _month_number(month_name)
        return "{0}-{1}-{2:0>2}".format(year, month, day) if month else None
    month_year = re.fullmatch(r"([A-Za-z]+)\s+(\d{4})", cleaned)
    if month_year:
        month_name, year = month_year.groups()
        month = _month_number(month_name)
        return "{0}-{1}-01".format(year, month) if month else None
    return None


def _month_number(month_name: str) -> Optional[str]:
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
    return months.get(month_name.lower())


def _extract_policy_number_from_text(text: str) -> Optional[str]:
    match = re.search(r"Policy Number:\s*([A-Z0-9-]+)", text, flags=re.IGNORECASE)
    return match.group(1).upper() if match else None


def _first_meaningful_line(text: str) -> Optional[str]:
    for line in text.splitlines():
        cleaned = line.strip()
        if cleaned and "Page " not in cleaned and "January 2026" not in cleaned:
            return cleaned[:220]
    return None


def _normalize_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _dedupe_lines(lines: List[str]) -> List[str]:
    cleaned = []
    seen = set()
    for line in lines:
        normalized = re.sub(r"\s+", " ", line).strip(" ;.")
        if not normalized:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(normalized)
    return cleaned


def _join_notes(existing: Optional[str], addition: Optional[str]) -> Optional[str]:
    pieces = [piece for piece in [existing, addition] if piece]
    if not pieces:
        return None
    return " ".join(dict.fromkeys(pieces))
