import numpy as np
from core.config import DEFAULT_WEIGHT
from core.graph.memory_graph import MemoryGraph

class ScoringEngine:
    def __init__(self, graph: MemoryGraph, alpha: float = 0.4, beta: float = 0.6):
        self.g = graph; self.alpha = alpha; self.beta = beta

    def skill_matrix(self, weights: dict[str, int]) -> np.ndarray:
        g = self.g
        S = np.zeros((len(g.people), len(g.projects)))
        for j, proj in enumerate(g.projects):
            num = np.zeros(len(g.people)); den = 0.0
            for rq in proj.requirements:
                w = float(weights.get(rq.skill, DEFAULT_WEIGHT))
                lv = g.skill_levels[:, g.skill_index[rq.skill]]
                num += w * np.minimum(lv / rq.min_level, 1.0)
                den += w
            S[:, j] = num / den
        return S

    def synergy_matrix(self) -> np.ndarray:
        g = self.g
        cw = np.minimum(g.cowork_months.toarray() / 12.0, 1.0)
        C = self.alpha * cw
        for (i, j), v in g.pair_review_score.items():
            C[i, j] += self.beta * v; C[j, i] += self.beta * v
        np.fill_diagonal(C, 0.0)
        return np.clip(C, -1.0, 1.0)
