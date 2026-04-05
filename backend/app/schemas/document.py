from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class DocumentCreate(BaseModel):
    plan_id: str
    file_name: str
    title: Optional[str] = None
    file_url: Optional[str] = None
    document_type: str = "formulary"
    source_url: Optional[str] = None
    raw_text: Optional[str] = None
    status: str = "processed"
    payer: Optional[str] = None
    policy_number: Optional[str] = None
    effective_date: Optional[str] = None
    last_reviewed_date: Optional[str] = None
    version: Optional[int] = None
    previous_version_id: Optional[str] = None
    policy_fingerprint: Optional[str] = None


class DocumentRead(DocumentCreate):
    id: str
    created_at: str
    updated_at: str
