"""규모별 데이터셋 생성. 동일 시드로 만들어 세 백엔드에 같은 데이터를 적재한다."""
from functools import lru_cache
from typing import Tuple

from core.datagen.generator import generate_dataset, Dataset
from core.datagen.parse_reviews import parse_reviews_rule_based, ParsedReview
from core.graph.memory_graph import MemoryGraph

SCALES = [(50, 10), (100, 20), (200, 40), (300, 60), (500, 100), (1000, 200)]


@lru_cache(maxsize=None)
def build_scale(n_people: int, n_projects: int, seed: int = 42) -> Tuple[Dataset, list[ParsedReview], MemoryGraph]:
    """
    규모별 데이터셋과 그래프를 생성한다.

    주의: 이 함수는 @lru_cache로 memoize되어 있으므로, 반환된 Dataset/ParsedReview 리스트/MemoryGraph 객체는
    동일한 매개변수로 호출한 모든 caller 간에 공유된다. 반환된 객체를 절대로 변경하면 안 된다 —
    변경하면 캐시에 저장된 객체가 오염되어 후속 caller 모두에게 손상된 데이터를 전달한다.
    """
    ds = generate_dataset(n_people, n_projects, seed)
    parsed = parse_reviews_rule_based(ds.reviews)
    return ds, parsed, MemoryGraph.build(ds, parsed)
