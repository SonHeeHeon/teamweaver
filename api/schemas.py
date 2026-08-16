from pydantic import BaseModel


class PersonOut(BaseModel):
    id: str
    name: str
    grade: str
    skills: dict[str, int]


class ProjectOut(BaseModel):
    id: str
    name: str
    sector: str
    phase: str
    start_month: int
    end_month: int
    grade_headcount: dict[str, int]
    monthly_budget: int


class MetaResponse(BaseModel):
    people: list[PersonOut]
    projects: list[ProjectOut]
    skills: list[str]
    review_items: list[str]


class BriefingOut(BaseModel):
    rationale: str
    risks: list[str]
    alternatives: list[str]


class WhatifResponse(BaseModel):
    objective_delta: float
    briefing: BriefingOut
    fallback_used: bool
