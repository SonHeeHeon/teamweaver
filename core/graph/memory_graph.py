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
    node_polarity: dict[int, float] = field(default_factory=dict)

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
        pol_acc: dict[int, list[float]] = defaultdict(list)
        for p_ in parsed:
            pol_acc[pid[p_.reviewee_id]].append(p_.text_polarity)
        node_pol = {k: float(np.mean(v)) for k, v in pol_acc.items()}
        return cls(ds.people, ds.projects, pid, jidx, sidx, L, cw, pair_score, dict(ev), node_pol)

    def synergy_context_memory(self, person_ids: list[str],
                               hops: int) -> list[tuple[str, str, float | None]]:
        """hops 이내 도달 인력과 그들의 평균 리뷰 극성.

        sqlite_store.synergy_context_sql / neo4j_store.synergy_context_cypher 와
        동일 의미론: (src, node, avg_polarity), node != src, 도달 집합은 hops
        이내 전부. 희소행렬 프론티어 확장으로 계산한다 — 이것이 인메모리 방식의
        정직한 구현이며, 실험 1의 세 번째 비교 주체다.
        """
        adj = (self.cowork_months > 0)          # 불린 인접행렬
        ids = [p.id for p in self.people]
        out: list[tuple[str, str, float | None]] = []
        for pid_str in person_ids:
            src = self.pid_index[pid_str]
            frontier = np.zeros(len(ids), dtype=bool)
            frontier[src] = True
            reached = frontier.copy()
            for _ in range(hops):
                nxt = (adj @ frontier.astype(np.int8)) > 0     # 한 홉 확장 (cowork_months는 대칭이므로 .T 불필요)
                frontier = nxt & ~reached
                if not frontier.any():
                    break
                reached |= frontier
            reached[src] = False
            for idx in np.flatnonzero(reached):
                out.append((pid_str, ids[int(idx)], self.node_polarity.get(int(idx))))
        return out
