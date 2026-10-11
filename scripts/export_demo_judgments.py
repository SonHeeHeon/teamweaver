"""가상 시연 묶음의 리뷰 글 LLM 판정을 저장소에 동봉한다(`demo/review_judgments.json`, 사용자 요청 2026-10-11).

왜: LLM은 같은 글도 매번 조금씩 다르게 매긴다. 판정값은 데이터 버전에 들어가고, 미리 계산(`demo/precomputed`)은 그 버전이
같을 때만 쓰인다 -- 다른 기기·빈 데이터 폴더에서 다시 판정하면 미리 계산 6개가 조용히 건너뛰어진다. 이 파일이 있으면 서버가
가상 묶음을 켤 때 캐시에 없는 판정을 여기서 채운다(`api.review_judge.judge_reviews(seed_path=)`).

무엇을: 이 기기의 판정 캐시(`<데이터 폴더>/review_judgments_synthetic.json`)에서 시연 묶음(`TEAMWEAVER_DEMO_DIR`, 기본 `demo/`)
모든 리뷰의 키만 골라 쓴다. 키는 지금 판정기(주소·모델·추론 강도) 기준이다 -- 판정기가 바뀌면 쓰이지 않는다.
LLM을 부르지 않는다. 캐시에 없는 리뷰가 있으면(그 묶음을 이 기기에서 켠 적이 없음) 쓰지 않고 실패한다.
가상(`synthetic: true`) 묶음만 담는다(실데이터 평가 원문의 판정은 저장소에 넣지 않는다).

    uv run python scripts/export_demo_judgments.py            # 쓰기
    uv run python scripts/export_demo_judgments.py --check    # 동봉 파일이 지금 묶음·판정기를 모두 덮는지 확인만

미리 계산(`python -m rehearsal.precompute_demo`)을 다시 만들면 이것도 다시 돌려 함께 커밋한다.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def _bundle_keys(root: Path) -> tuple[list[str], bool]:
    """묶음 하나의 판정 키와 가상 여부."""
    from api import review_judge as rj
    from api.datasets import extract_bundle_zip
    from core.ingest.convert import to_dataset
    from core.ingest.loader import load_bundle
    if root.suffix.lower() == ".zip":
        with tempfile.TemporaryDirectory(prefix="teamweaver-export-") as tmp:
            return _bundle_keys(extract_bundle_zip(root.read_bytes(), Path(tmp) / "bundle"))
    bundle, report = load_bundle(root)
    ds, _ = to_dataset(bundle, report)
    return rj.cache_keys(ds), bundle.manifest.get("synthetic") is True


def collect() -> tuple[dict[str, float], dict[str, int], dict[str, int]]:
    """(동봉할 판정, 묶음별 리뷰 수, 묶음별 캐시에 없는 수)."""
    from api import review_judge as rj
    from api.datasets import judge_cache_path
    from api.demos import demo_root, list_demos
    cache = rj._load_cache(judge_cache_path(True))
    out: dict[str, float] = {}
    sizes: dict[str, int] = {}
    missing: dict[str, int] = {}
    for d in list_demos():
        root = demo_root(d["name"])
        if root is None:
            continue
        keys, synthetic = _bundle_keys(root)
        if not synthetic:
            continue
        sizes[d["name"]] = len(keys)
        missing[d["name"]] = sum(1 for k in set(keys) if k not in cache)
        out.update({k: cache[k] for k in keys if k in cache})
    return out, sizes, missing


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="동봉 파일이 지금 묶음·판정기를 모두 덮는지만 확인")
    args = ap.parse_args(argv)
    from api import review_judge as rj
    from api.demos import demo_judgments_path
    target = demo_judgments_path()
    ep = rj.endpoint()
    print(f"판정기: {ep['model']} @ {ep['host']} · 추론 강도 {rj.reasoning_effort() or '없음'}")
    if args.check:
        from api.demos import demo_root, list_demos
        seed = rj._load_cache(target)
        bad = 0
        for d in list_demos():
            root = demo_root(d["name"])
            keys, synthetic = _bundle_keys(root) if root else ([], False)
            if not synthetic:
                continue
            miss = sum(1 for k in set(keys) if k not in seed)
            bad += miss
            print(f"  {d['name']}: 리뷰 {len(keys)}건 · 동봉에 없음 {miss}")
        print("OK" if bad == 0 else f"동봉 파일이 {bad}건을 덮지 못한다")
        return 0 if bad == 0 else 1
    judged, sizes, missing = collect()
    for name, n in sizes.items():
        print(f"  {name}: 리뷰 {n}건 · 캐시에 없음 {missing[name]}")
    if any(missing.values()):
        print("캐시에 없는 판정이 있다 -- 그 묶음을 이 기기에서 한 번 켜 판정한 뒤 다시 돌린다(쓰지 않음).")
        return 1
    target.write_text(json.dumps(judged, sort_keys=True, indent=0) + "\n", "utf-8")
    print(f"썼다: {target} ({len(judged)}건)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
