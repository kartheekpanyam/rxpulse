from __future__ import annotations

import hashlib
import re
from typing import Dict, List, Optional

import httpx

from app.config import Settings, get_settings
from app.schemas.document import DocumentCreate
from app.schemas.drug_coverage import DrugCoverageCreate
from app.schemas.plan import PlanCreate


class SupabaseService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def is_configured(self) -> bool:
        return bool(self.settings.supabase_url and self.settings.supabase_key)

    def status(self) -> Dict[str, bool]:
        return {"configured": self.is_configured}

    def check_connection(self) -> Dict:
        if not self.is_configured:
            return {"connected": False, "error": "Supabase URL or key missing"}
        try:
            response = httpx.get(
                "{0}/auth/v1/settings".format(self.settings.supabase_url.rstrip("/")),
                headers={"apikey": self.settings.supabase_key},
                timeout=15.0,
            )
            response.raise_for_status()
            return {"connected": True, "project_url": self.settings.supabase_url}
        except Exception as exc:
            return {"connected": False, "error": str(exc)}

    # -------------------------------------------------------------------------
    # Plans
    # -------------------------------------------------------------------------

    def list_plans(self) -> List[Dict]:
        return self._request("GET", "/rest/v1/plans", params={"select": "*", "order": "created_at.desc", "limit": "50"}).json()

    def create_plan(self, payload: PlanCreate) -> Dict:
        rows = self._request("POST", "/rest/v1/plans", headers={"Prefer": "return=representation"}, json=payload.model_dump()).json()
        return rows[0] if rows else {}

    def find_or_create_plan_for_payer(
        self,
        payer_name: str,
        plan_name: Optional[str] = None,
        state: Optional[str] = None,
        plan_year: Optional[int] = 2026,
        plan_type: Optional[str] = "Medical Policy",
    ) -> str:
        """Return existing plan_id for payer or create a new one."""
        rows = self._request("GET", "/rest/v1/plans", params={
            "select": "id,plan_name,state,plan_year,plan_type",
            "insurer_name": "ilike.*{0}*".format(payer_name),
            "limit": "1",
        }).json()
        if rows:
            existing = rows[0]
            patch = {}
            if plan_name and existing.get("plan_name") in {None, "", "Medical Benefit Drug Policy"}:
                patch["plan_name"] = plan_name
            if state and not existing.get("state"):
                patch["state"] = state
            if plan_year and not existing.get("plan_year"):
                patch["plan_year"] = plan_year
            if plan_type and not existing.get("plan_type"):
                patch["plan_type"] = plan_type
            if patch:
                self._request(
                    "PATCH",
                    "/rest/v1/plans",
                    headers={"Prefer": "return=minimal"},
                    params={"id": "eq.{0}".format(existing["id"])},
                    json=patch,
                )
            return existing["id"]
        new_plan = self._request("POST", "/rest/v1/plans", headers={"Prefer": "return=representation"}, json={
            "insurer_name": payer_name,
            "plan_name": plan_name or "Medical Benefit Drug Policy",
            "plan_year": plan_year,
            "state": state,
            "plan_type": plan_type,
            "source": "upload",
        }).json()
        return new_plan[0]["id"] if new_plan else ""

    # -------------------------------------------------------------------------
    # Documents
    # -------------------------------------------------------------------------

    def list_documents(self, plan_id: Optional[str] = None) -> List[Dict]:
        params = {"select": "*", "order": "created_at.desc", "limit": "50"}
        if plan_id:
            params["plan_id"] = "eq.{0}".format(plan_id)
        return self._request("GET", "/rest/v1/documents", params=params).json()

    def get_document(self, document_id: str) -> Dict:
        rows = self._request("GET", "/rest/v1/documents", params={"select": "*", "id": "eq.{0}".format(document_id), "limit": "1"}).json()
        return rows[0] if rows else {}

    def create_document(self, payload: DocumentCreate) -> Dict:
        rows = self._request("POST", "/rest/v1/documents", headers={"Prefer": "return=representation"}, json=payload.model_dump()).json()
        return rows[0] if rows else {}

    def update_document_metadata(self, document_id: str, payload: Dict) -> Dict:
        rows = self._request("PATCH", "/rest/v1/documents", headers={"Prefer": "return=representation"},
                             params={"id": "eq.{0}".format(document_id)}, json=payload).json()
        return rows[0] if rows else {}

    def find_previous_version(self, payer: str, policy_number: Optional[str]) -> Optional[Dict]:
        """Find the most recent document for same payer+policy_number fingerprint."""
        fingerprint = self._make_fingerprint(payer, policy_number)
        rows = self._request("GET", "/rest/v1/documents", params={
            "select": "*",
            "policy_fingerprint": "eq.{0}".format(fingerprint),
            "order": "created_at.desc",
            "limit": "1",
        }).json()
        return rows[0] if rows else None

    def _make_fingerprint(self, payer: str, policy_number: Optional[str]) -> str:
        base = "{0}|{1}".format(payer.lower().strip(), (policy_number or "").lower().strip())
        return hashlib.md5(base.encode()).hexdigest()

    # -------------------------------------------------------------------------
    # Drug coverages
    # -------------------------------------------------------------------------

    def list_drug_coverages(self, plan_id: Optional[str] = None, document_id: Optional[str] = None) -> List[Dict]:
        params = {"select": "*", "order": "created_at.desc", "limit": "200"}
        if plan_id:
            params["plan_id"] = "eq.{0}".format(plan_id)
        if document_id:
            params["document_id"] = "eq.{0}".format(document_id)
        rows = self._request("GET", "/rest/v1/drug_coverages", params=params).json()
        return rows if document_id else self._filter_to_latest_document_rows(rows)

    def search_drug_coverages(self, drug: str, payer: Optional[str] = None, limit: int = 50) -> List[Dict]:
        query = drug.strip().lower()
        if not query:
            return []

        params = {"select": "*", "order": "updated_at.desc", "limit": "500"}
        if payer:
            params["payer"] = "ilike.*{0}*".format(payer)

        rows = self._request("GET", "/rest/v1/drug_coverages", params=params).json()
        rows = self._filter_to_latest_document_rows(rows)
        matches = [row for row in rows if self._row_matches_drug_query(row, query)]
        matches.sort(key=lambda row: (
            -self._match_strength(row, query),
            -(row.get("confidence_score") or 0),
            row.get("payer") or "",
            row.get("policy_name") or "",
        ))
        return matches[:limit]

    def compare_drug_across_payers(self, drug: str, payers: Optional[List[str]] = None, limit: int = 50) -> List[Dict]:
        rows = self.search_drug_coverages(drug=drug, limit=limit)
        if payers:
            filtered = [r for r in rows if any(p.lower() in (r.get("payer") or "").lower() for p in payers)]
            rows = filtered if filtered else rows
        seen: Dict[str, Dict] = {}
        for row in rows:
            key = "|".join([
                (row.get("payer") or "unknown").lower(),
                str(row.get("product_key") or row.get("generic_name") or row.get("drug_name") or "").lower(),
                str(row.get("policy_number") or row.get("policy_name") or "").lower(),
            ])
            if key not in seen or (row.get("confidence_score") or 0) > (seen[key].get("confidence_score") or 0):
                seen[key] = row
        return sorted(
            seen.values(),
            key=lambda row: (
                row.get("payer") or "",
                row.get("family_name") or row.get("generic_name") or row.get("drug_name") or "",
                row.get("product_name") or "",
            ),
        )

    def replace_drug_coverages_for_document(self, document_id: str, payloads: List[DrugCoverageCreate]) -> List[Dict]:
        self._request("DELETE", "/rest/v1/drug_coverages", headers={"Prefer": "return=minimal"},
                      params={"document_id": "eq.{0}".format(document_id)})
        if not payloads:
            return []
        return self._request("POST", "/rest/v1/drug_coverages", headers={"Prefer": "return=representation"},
                             json=[p.model_dump() for p in payloads]).json()

    # -------------------------------------------------------------------------
    # RAG: document chunks
    # -------------------------------------------------------------------------

    def save_chunks(self, document_id: str, chunks: List[Dict]) -> None:
        """Store tagged chunks for RAG retrieval."""
        if not chunks:
            return
        # Delete old chunks for this doc first
        self._request("DELETE", "/rest/v1/document_chunks", headers={"Prefer": "return=minimal"},
                      params={"document_id": "eq.{0}".format(document_id)})
        rows = [
            {
                "document_id": document_id,
                "chunk_index": c["chunk_index"],
                "content": c["content"],
                "payer": c.get("payer"),
                "drug_name": c.get("drug_name"),
                "section_type": c.get("section_type", "general"),
                "page_number": c.get("page_number"),
                "metadata": c.get("metadata") or {},
            }
            for c in chunks
        ]
        # Insert in batches of 50
        for i in range(0, len(rows), 50):
            batch = rows[i:i + 50]
            self._request("POST", "/rest/v1/document_chunks", headers={"Prefer": "return=minimal"}, json=batch)

    def retrieve_chunks_for_question(self, question: str, limit: int = 12) -> List[Dict]:
        """Retrieve relevant chunks for a natural language question using keyword matching."""
        stop = {"what", "which", "does", "cover", "the", "for", "and", "plan", "plans",
                "drug", "require", "criteria", "prior", "auth", "authorization", "how",
                "differ", "between", "payer", "payers", "policy", "policies", "medical",
                "benefit", "insurance", "about", "when", "where", "that", "this", "with"}
        tokens = [w for w in re.findall(r"[a-zA-Z]{3,}", question.lower()) if w not in stop]

        seen_ids: set = set()
        rows: List[Dict] = []

        # Search by tagged drug_name and chunk content keywords
        for token in tokens[:5]:
            drug_rows = self._request("GET", "/rest/v1/document_chunks", params={
                "select": "*",
                "drug_name": "ilike.*{0}*".format(token),
                "limit": "10",
            }).json()
            for r in drug_rows:
                if r.get("id") not in seen_ids:
                    rows.append(r)
                    seen_ids.add(r.get("id"))

            # Then content match
            content_rows = self._request("GET", "/rest/v1/document_chunks", params={
                "select": "*",
                "content": "ilike.*{0}*".format(token),
                "limit": "10",
            }).json()
            for r in content_rows:
                if r.get("id") not in seen_ids:
                    rows.append(r)
                    seen_ids.add(r.get("id"))

        # Prioritize rows where the chunk metadata carries stronger drug or policy signals.
        rows = self._filter_to_latest_chunk_rows(rows)
        rows.sort(key=lambda r: (
            0 if r.get("section_type") != "general" else 1,
            0 if (r.get("drug_name") or ((r.get("metadata") or {}).get("matched_alias"))) else 1,
        ))

        if not rows:
            rows = self._request("GET", "/rest/v1/document_chunks", params={
                "select": "*", "order": "created_at.desc", "limit": str(limit),
            }).json()
            rows = self._filter_to_latest_chunk_rows(rows)

        return rows[:limit]

    # -------------------------------------------------------------------------
    # Policy changes (persistent diffs)
    # -------------------------------------------------------------------------

    def save_policy_changes(self, changes: List[Dict]) -> None:
        """Persist a list of policy change records."""
        if not changes:
            return
        self._request("POST", "/rest/v1/policy_changes", headers={"Prefer": "return=minimal"}, json=changes)

    def list_policy_changes(self, payer: Optional[str] = None, drug: Optional[str] = None, limit: int = 50) -> List[Dict]:
        params = {"select": "*", "order": "change_date.desc,created_at.desc", "limit": str(limit)}
        if payer and payer != "All":
            params["payer"] = "ilike.*{0}*".format(payer)
        if drug and drug != "All":
            params["drug_name"] = "ilike.*{0}*".format(drug)
        return self._request("GET", "/rest/v1/policy_changes", params=params).json()

    # -------------------------------------------------------------------------
    # Dashboard stats
    # -------------------------------------------------------------------------

    def get_dashboard_stats(self, payer: Optional[str] = None) -> Dict:
        """Return aggregate stats for the dashboard."""
        # Payers tracked
        all_plans = self._request("GET", "/rest/v1/plans", params={"select": "insurer_name", "limit": "100"}).json()
        payers_tracked = len(set(p.get("insurer_name", "") for p in all_plans if p.get("insurer_name")))

        # Policies ingested (documents)
        docs_params = {"select": "id,payer,policy_fingerprint,version,created_at", "limit": "200"}
        if payer:
            docs_params["payer"] = "ilike.*{0}*".format(payer)
        all_docs = self._request("GET", "/rest/v1/documents", params=docs_params).json()
        latest_docs = self._latest_documents(all_docs)
        policies_ingested = len(latest_docs)

        # Drugs covered
        cov_params = {"select": "drug_name,generic_name,product_key,payer,coverage_status,prior_authorization,step_therapy,site_of_care", "limit": "500"}
        if payer:
            cov_params["payer"] = "ilike.*{0}*".format(payer)
        all_cov = self._request("GET", "/rest/v1/drug_coverages", params=cov_params).json()
        all_cov = self._filter_to_latest_document_rows(all_cov)
        drugs_covered = len(set(
            (r.get("product_key") or r.get("generic_name") or r.get("drug_name") or "").strip()
            for r in all_cov
            if (r.get("product_key") or r.get("generic_name") or r.get("drug_name"))
        ))

        # Changes this quarter
        changes_params = {"select": "id,change_type,payer,drug_name,change_date", "limit": "200"}
        if payer:
            changes_params["payer"] = "ilike.*{0}*".format(payer)
        all_changes = self._request("GET", "/rest/v1/policy_changes", params=changes_params).json()
        changes_this_quarter = len(all_changes)

        # PA rate, step therapy rate, site restriction rate
        pa_count = sum(1 for r in all_cov if r.get("prior_authorization"))
        step_count = sum(1 for r in all_cov if r.get("step_therapy"))
        site_count = sum(1 for r in all_cov if r.get("site_of_care"))
        total = len(all_cov) or 1

        # Top drugs by payer count
        drug_payer_map: Dict[str, set] = {}
        for r in all_cov:
            dn = r.get("drug_name", "")
            py = r.get("payer", "")
            if dn:
                drug_payer_map.setdefault(dn, set()).add(py)
        top_drugs = sorted(drug_payer_map.items(), key=lambda x: len(x[1]), reverse=True)[:8]

        # Change trend (by month from policy_changes)
        month_counts: Dict[str, int] = {}
        for c in all_changes:
            date = (c.get("change_date") or "")[:7]  # YYYY-MM
            if date:
                month_counts[date] = month_counts.get(date, 0) + 1
        change_trend = [{"month": k, "changes": v} for k, v in sorted(month_counts.items())[-7:]]

        # Change by type
        type_counts: Dict[str, int] = {}
        for c in all_changes:
            ct = c.get("change_type", "other")
            type_counts[ct] = type_counts.get(ct, 0) + 1

        # Recent changes feed
        recent_changes = all_changes[:5]

        # Per-payer restriction rates
        payer_stats: Dict[str, Dict] = {}
        for r in all_cov:
            py = r.get("payer") or "Unknown"
            if py not in payer_stats:
                payer_stats[py] = {"pa": 0, "step": 0, "site": 0, "total": 0}
            payer_stats[py]["total"] += 1
            if r.get("prior_authorization"):
                payer_stats[py]["pa"] += 1
            if r.get("step_therapy"):
                payer_stats[py]["step"] += 1
            if r.get("site_of_care"):
                payer_stats[py]["site"] += 1

        restriction_rates = [
            {
                "payer": py,
                "pa_required": round(v["pa"] / v["total"] * 100) if v["total"] else 0,
                "step_therapy": round(v["step"] / v["total"] * 100) if v["total"] else 0,
                "site_restrictions": round(v["site"] / v["total"] * 100) if v["total"] else 0,
            }
            for py, v in payer_stats.items()
        ]

        return {
            "payers_tracked": payers_tracked,
            "policies_ingested": policies_ingested,
            "drugs_covered": drugs_covered,
            "changes_this_quarter": changes_this_quarter,
            "pa_rate": round(pa_count / total * 100),
            "step_rate": round(step_count / total * 100),
            "site_rate": round(site_count / total * 100),
            "top_drugs": [{"drug": d, "payer_count": len(p)} for d, p in top_drugs],
            "change_trend": change_trend,
            "change_by_type": [{"name": k, "value": v} for k, v in type_counts.items()],
            "recent_changes": recent_changes,
            "restriction_rates": restriction_rates,
            "payer_list": sorted(payer_stats.keys()),
        }

    def build_coverage_matrix(self, drug: Optional[str] = None, payers: Optional[List[str]] = None, limit: int = 500) -> Dict:
        params = {"select": "*", "order": "payer.asc,generic_name.asc,product_name.asc", "limit": str(limit)}
        rows = self._request("GET", "/rest/v1/drug_coverages", params=params).json()
        rows = self._filter_to_latest_document_rows(rows)

        if drug:
            query = drug.strip().lower()
            rows = [row for row in rows if self._row_matches_drug_query(row, query)]
        if payers:
            rows = [row for row in rows if any(p.lower() in (row.get("payer") or "").lower() for p in payers)]

        payer_names = sorted({row.get("payer") for row in rows if row.get("payer")})
        grouped: Dict[str, Dict] = {}
        for row in rows:
            key = "|".join([
                str(row.get("family_name") or row.get("generic_name") or row.get("drug_name") or ""),
                str(row.get("product_key") or row.get("product_name") or ""),
            ])
            entry = grouped.setdefault(key, {
                "drug_name": row.get("drug_name"),
                "generic_name": row.get("generic_name"),
                "family_name": row.get("family_name"),
                "product_name": row.get("product_name"),
                "brand_names": row.get("brand_names") or [],
                "cells": [],
            })
            entry["brand_names"] = list(dict.fromkeys((entry.get("brand_names") or []) + (row.get("brand_names") or [])))
            entry["cells"].append({
                "payer": row.get("payer") or "Unknown",
                "policy_name": row.get("policy_name"),
                "policy_number": row.get("policy_number"),
                "coverage_status": row.get("coverage_status"),
                "coverage_bucket": row.get("coverage_bucket"),
                "prior_authorization": bool(row.get("prior_authorization")),
                "step_therapy": bool(row.get("step_therapy")),
                "quantity_limit": bool(row.get("quantity_limit")),
                "notes": row.get("notes"),
            })

        return {
            "payers": payer_names,
            "rows": sorted(
                grouped.values(),
                key=lambda row: (
                    row.get("family_name") or row.get("generic_name") or row.get("drug_name") or "",
                    row.get("product_name") or "",
                ),
            ),
        }

    def _row_matches_drug_query(self, row: Dict, query: str) -> bool:
        haystacks = [
            row.get("drug_name"),
            row.get("generic_name"),
            row.get("family_name"),
            row.get("product_name"),
            row.get("product_key"),
            row.get("policy_name"),
            row.get("hcpcs_code"),
        ]
        haystacks.extend(row.get("brand_names") or [])
        return any(query in str(value or "").lower() for value in haystacks)

    def _match_strength(self, row: Dict, query: str) -> int:
        ranked_values = [
            row.get("product_name"),
            row.get("product_key"),
            row.get("generic_name"),
            row.get("drug_name"),
            row.get("family_name"),
        ] + list(row.get("brand_names") or [])
        for index, value in enumerate(ranked_values):
            normalized = str(value or "").lower()
            if not normalized:
                continue
            if normalized == query:
                return 100 - index
            if query in normalized:
                return 75 - index
        return 0

    def _filter_to_latest_document_rows(self, rows: List[Dict]) -> List[Dict]:
        if not rows:
            return rows
        latest_ids = self._latest_document_id_set()
        filtered = []
        for row in rows:
            document_id = row.get("document_id")
            if not document_id or document_id in latest_ids:
                filtered.append(row)
        return filtered

    def _latest_document_id_set(self) -> set[str]:
        docs = self._request(
            "GET",
            "/rest/v1/documents",
            params={"select": "id,policy_fingerprint,version,created_at", "limit": "500"},
        ).json()
        latest = self._latest_documents(docs)
        return {doc["id"] for doc in latest if doc.get("id")}

    def _latest_documents(self, docs: List[Dict]) -> List[Dict]:
        latest_by_group: Dict[str, Dict] = {}
        for doc in docs:
            group_key = str(doc.get("policy_fingerprint") or doc.get("id") or "")
            existing = latest_by_group.get(group_key)
            if not existing:
                latest_by_group[group_key] = doc
                continue

            existing_version = existing.get("version") or 1
            incoming_version = doc.get("version") or 1
            if incoming_version > existing_version:
                latest_by_group[group_key] = doc
                continue
            if incoming_version == existing_version and str(doc.get("created_at") or "") > str(existing.get("created_at") or ""):
                latest_by_group[group_key] = doc

        return list(latest_by_group.values())

    def _filter_to_latest_chunk_rows(self, rows: List[Dict]) -> List[Dict]:
        if not rows:
            return rows
        latest_ids = self._latest_document_id_set()
        filtered = []
        for row in rows:
            document_id = row.get("document_id")
            if not document_id or document_id in latest_ids:
                filtered.append(row)
        return filtered

    # -------------------------------------------------------------------------
    # Legacy QA helper (kept for backward compat)
    # -------------------------------------------------------------------------

    def fetch_coverages_for_qa(self, question: str, limit: int = 30) -> list:
        import re as _re
        stop = {"what", "which", "does", "cover", "the", "for", "and", "plan", "plans",
                "drug", "require", "criteria", "prior", "auth", "authorization"}
        tokens = [w for w in _re.findall(r"[a-zA-Z]{3,}", question.lower()) if w not in stop]
        rows: list = []
        seen_ids: set = set()
        for token in tokens[:4]:
            partial = self.search_drug_coverages(drug=token, limit=20)
            for r in partial:
                if r.get("id") not in seen_ids:
                    rows.append(r)
                    seen_ids.add(r.get("id"))
        if not rows:
            params = {"select": "*", "order": "created_at.desc", "limit": str(limit)}
            rows = self._request("GET", "/rest/v1/drug_coverages", params=params).json()
        return rows[:limit]

    # -------------------------------------------------------------------------
    # HTTP helper
    # -------------------------------------------------------------------------

    def _request(self, method: str, path: str, headers: Optional[Dict] = None,
                 params: Optional[Dict] = None, json: Optional[object] = None) -> httpx.Response:
        if not self.is_configured:
            raise ValueError("Supabase URL or key missing")
        base_headers = {
            "apikey": self.settings.supabase_key,
            "Authorization": "Bearer {0}".format(self.settings.supabase_key),
            "Content-Type": "application/json",
        }
        if headers:
            base_headers.update(headers)
        response = httpx.request(
            method,
            "{0}{1}".format(self.settings.supabase_url.rstrip("/"), path),
            headers=base_headers,
            params=params,
            json=json,
            timeout=30.0,
        )
        response.raise_for_status()
        return response


def get_supabase_service(settings: Optional[Settings] = None) -> SupabaseService:
    return SupabaseService(settings or get_settings())
