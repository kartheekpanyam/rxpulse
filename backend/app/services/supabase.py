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
        return self._request("GET", "/rest/v1/drug_coverages", params=params).json()

    def search_drug_coverages(self, drug: str, payer: Optional[str] = None, limit: int = 50) -> List[Dict]:
        params = {"select": "*", "order": "drug_name.asc", "limit": str(limit), "drug_name": "ilike.*{0}*".format(drug)}
        if payer:
            params["payer"] = "ilike.*{0}*".format(payer)
        return self._request("GET", "/rest/v1/drug_coverages", params=params).json()

    def compare_drug_across_payers(self, drug: str, payers: Optional[List[str]] = None, limit: int = 50) -> List[Dict]:
        rows = self.search_drug_coverages(drug=drug, limit=limit)
        if payers:
            filtered = [r for r in rows if any(p.lower() in (r.get("payer") or "").lower() for p in payers)]
            rows = filtered if filtered else rows
        seen: Dict[str, Dict] = {}
        for row in rows:
            key = (row.get("payer") or "unknown").lower()
            if key not in seen or (row.get("confidence_score") or 0) > (seen[key].get("confidence_score") or 0):
                seen[key] = row
        return list(seen.values())

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
                "metadata": {},
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

        # Search by drug_name and content keywords
        for token in tokens[:5]:
            # Try drug_name match first
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

        # Prioritize non-general sections
        rows.sort(key=lambda r: 0 if r.get("section_type") != "general" else 1)

        # Fallback: return recent chunks if nothing found
        if not rows:
            rows = self._request("GET", "/rest/v1/document_chunks", params={
                "select": "*", "order": "created_at.desc", "limit": str(limit),
            }).json()

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
        docs_params = {"select": "id,payer", "limit": "200"}
        if payer:
            docs_params["payer"] = "ilike.*{0}*".format(payer)
        all_docs = self._request("GET", "/rest/v1/documents", params=docs_params).json()
        policies_ingested = len(all_docs)

        # Drugs covered
        cov_params = {"select": "drug_name,coverage_status,payer,prior_authorization,step_therapy,site_of_care", "limit": "500"}
        if payer:
            cov_params["payer"] = "ilike.*{0}*".format(payer)
        all_cov = self._request("GET", "/rest/v1/drug_coverages", params=cov_params).json()
        drugs_covered = len(set(r.get("drug_name", "") for r in all_cov if r.get("drug_name")))

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