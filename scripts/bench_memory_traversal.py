"""in-memory n-hop 순회(synergy_context_memory) wall-clock 측정: \
   uv run python scripts/bench_memory_traversal.py --people 1000 --projects 200 --seed 42

측정 유틸리티일 뿐 테스트가 아니다 — 목표치를 검증하지 않고 현재 값을 출력한다.
Task 4(3자 벤치마크)가 SQLite/Neo4j 대비 in-memory 백엔드의 순회 비용을 참고할 때 쓴다.
"""
import argparse
import time
from core.datagen.generator import generate_dataset
from core.datagen.parse_reviews import parse_reviews_rule_based
from core.graph.memory_graph import MemoryGraph

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--people", type=int, default=1000)
    ap.add_argument("--projects", type=int, default=200)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--n-seeds", type=int, default=5, help="synergy_context_memory에 넘길 시드 인원 수")
    ap.add_argument("--repeats", type=int, default=5, help="hop당 반복 측정 횟수(평균 산출)")
    args = ap.parse_args()

    t0 = time.perf_counter()
    ds = generate_dataset(args.people, args.projects, args.seed)
    t1 = time.perf_counter()
    parsed = parse_reviews_rule_based(ds.reviews)
    t2 = time.perf_counter()
    g = MemoryGraph.build(ds, parsed)
    t3 = time.perf_counter()

    print(f"people={len(ds.people)} projects={len(ds.projects)} "
          f"coworks={len(ds.coworks)} reviews={len(ds.reviews)}")
    print(f"generate_dataset={t1-t0:.3f}s parse_reviews={t2-t1:.3f}s "
          f"MemoryGraph.build={t3-t2:.3f}s")

    seeds = [p.id for p in ds.people[:args.n_seeds]]
    for hops in (1, 2, 3, 4):
        start = time.perf_counter()
        for _ in range(args.repeats):
            rows = g.synergy_context_memory(seeds, hops)
        elapsed = time.perf_counter() - start
        avg_ms = elapsed / args.repeats * 1000
        print(f"hops={hops}: reached_rows={len(rows)} "
              f"avg_wall_clock({args.repeats} runs)={avg_ms:.3f}ms")

if __name__ == "__main__":
    main()
