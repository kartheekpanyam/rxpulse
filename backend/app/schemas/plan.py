from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class PlanCreate(BaseModel):
    insurer_name: str
    plan_name: str
    plan_year: Optional[int] = None
    state: Optional[str] = None
    plan_type: Optional[str] = None
    source: str = "manual"


class PlanRead(PlanCreate):
    id: str
    created_at: str
    updated_at: str
