from collections import defaultdict
from dataclasses import dataclass, field
import numpy as np
from scipy.sparse import csr_matrix
from core.domain.models import Dataset, ParsedReview, PeerReview, Person, Project

def _item_score(r: PeerReview) -> float:
    np_, nn = len(r.positive.items), len(r.negative.items)
    return (np_ - nn) / (np_ + nn)

@dataclass
class MemoryGraph:
    people: list[Person]
    projects: list[Project]
    pid_index: dict[str, int]
    project_index: dict[str, int]
    skill_index: dict[str, int]
    skill_levels: np.ndarray
    cowork_months: csr_matrix
    pair_review_score: dict[tuple[int, int], float]
    pair_evidence: dict[tuple[int, int], list[tuple[str, str]]] = field(default_factory=dict)

    @classmethod
    def build(cls, ds: Dataset, parsed: list[ParsedReview]) -> "MemoryGraph":
        pid = {p.id: i for i, p in enumerate(ds.people)}
        jidx = {j.id: k for k, j in enumerate(ds.projects)}
        skills = sorted({s for p in ds.people for s in p.skills}
                        | {r.skill for j in ds.projects for r in j.requirements})
        sidx = {s: k for k, s in enumerate(skills)}
        L = np.zeros((len(ds.people), len(skills)))
        for p in ds.people:
            for s, lv in p.skills.items():
                L[pid[p.id], sidx[s]] = lv
        n = len(ds.people)
        rows, cols, vals = [], [], []
        for c in ds.coworks:
            i, j = pid[c.a_id], pid[c.b_id]
            rows += [i, j]; cols += [j, i]; vals += [c.co_months, c.co_months]
        cw = csr_matrix((vals, (rows, cols)), shape=(n, n))

        pol = {(r.reviewer_id, r.reviewee_id): r.text_polarity for r in parsed}
        acc: dict[tuple[int, int], list[float]] = defaultdict(list)
        ev: dict[tuple[int, int], list[tuple[str, str]]] = defaultdict(list)
        pr_by_dir = {(r.reviewer_id, r.reviewee_id): r for r in ds.reviews}
        for (rv, re_), r in pr_by_dir.items():
            key = tuple(sorted((pid[rv], pid[re_])))
            score = 0.5 * _item_score(r) + 0.5 * pol.get((rv, re_), 0.0)
            acc[key].append(score)
        for p_ in parsed:
            key = tuple(sorted((pid[p_.reviewer_id], pid[p_.reviewee_id])))
            for evidence in p_.evidence:
                ev[key].append((p_.reviewer_id, evidence))
        pair_score = {k: float(np.mean(v)) for k, v in acc.items()}
        return cls(ds.people, ds.projects, pid, jidx, sidx, L, cw, pair_score, dict(ev))
