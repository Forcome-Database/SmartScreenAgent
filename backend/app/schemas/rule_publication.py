from __future__ import annotations

from pydantic import BaseModel, Field


class CreateDraftRequest(BaseModel):
    rule_schema: dict = Field(alias="schema_json")
    notes: str | None = None


class RuleVersionRef(BaseModel):
    id: int
    version: str
    status: str
    notes: str | None
