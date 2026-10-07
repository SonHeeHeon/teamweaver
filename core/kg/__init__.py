"""지식 그래프: 그래프 하나(graph.build_kg) + 용도별 보기(views). 설계 .omc/plan/2026-10-07-knowledge-graph.md."""
from core.kg.graph import KnowledgeGraph, build_kg, with_plan
from core.kg.views import project_evidence, skill_map

__all__ = ["KnowledgeGraph", "build_kg", "with_plan", "project_evidence", "skill_map"]
