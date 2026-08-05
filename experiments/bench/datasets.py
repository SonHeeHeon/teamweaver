"""규모별 데이터셋 생성. 동일 시드로 만들어 세 백엔드에 같은 데이터를 적재한다."""
from functools import lru_cache

from core.datagen.generator import generate_dataset
from core.datagen.parse_reviews import parse_reviews_rule_based
from core.graph.memory_graph import MemoryGraph

SCALES = [(50, 10), (100, 20), (200, 40), (300, 60), (500, 100), (1000, 200)]


@lru_cache(maxsize=None)
def build_scale(n_people: int, n_projects: int, seed: int = 42):
    ds = generate_dataset(n_people, n_projects, seed)
    parsed = parse_reviews_rule_based(ds.reviews)
    return ds, parsed, MemoryGraph.build(ds, parsed)
