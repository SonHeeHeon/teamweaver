"""로컬 LLM 체크포인트(.omc/llm_checkpoint/, gitignored)를 커밋 가능한 redacted
요약으로 집계한다.

최종 리뷰 Important 10: exp2_pipeline.py의 `output_token_assumption_basis`와
`_latency()`의 근거가 전부 `.omc/`·`.superpowers/` 밑을 가리켰다 — 둘 다
`.gitignore` 대상이라 클론한 사람은 그 경로를 열어볼 수 없다. exp1/exp3는
`main()`을 재실행해 직접 검증할 수 있지만, exp2의 이 상수 하나는 검증할
방법이 없었다.

이 스크립트는 체크포인트 파일에서 **모델별 토큰 총계·호출 수·비용**만 뽑아
`experiments/results/llm_checkpoint_summary.json`(git 추적 대상)에 쓴다.
프롬프트 원문·리뷰 텍스트·evidence는 절대 옮기지 않는다 — `usage_summary()`
(core/datagen/llm_checkpoint.py)가 애초에 `gen_usage`/`parse_usage`(토큰 카운트)
필드만 읽으므로 원문에 접근하지 않는다.

API 호출 없음 — 이미 로컬에 존재하는 체크포인트 파일을 읽기만 한다. 체크포인트가
없는 환경(신규 클론)에서는 조용히 건너뛴다(재실행 불가능한 로컬 아티팩트이므로
결과 스위트의 필수 단계가 아니다).
"""
import json

from core.config import FIXTURES_DIR, REPO_ROOT, load_pricing
from core.datagen.llm_checkpoint import load_checkpoint, usage_summary

CHECKPOINT_DIR = REPO_ROOT / ".omc" / "llm_checkpoint"
OUT_PATH = REPO_ROOT / "experiments" / "results" / "llm_checkpoint_summary.json"


def _fixture_meta() -> dict:
    path = FIXTURES_DIR / "meta.json"
    return json.loads(path.read_text("utf-8")) if path.exists() else {}


def build() -> dict | None:
    meta = _fixture_meta()
    n_people, n_projects = meta.get("n_people"), meta.get("n_projects")
    seed = meta.get("seed")
    pricing = load_pricing()
    gen_model = meta.get("gen_model") or pricing["gen_model"]
    parse_model = meta.get("parse_model") or pricing["parse_model"]
    if n_people is None or n_projects is None or seed is None:
        return None
    checkpoint_path = CHECKPOINT_DIR / f"seed{seed}_p{n_people}_pr{n_projects}.json"
    if not checkpoint_path.exists():
        return None

    checkpoint = load_checkpoint(checkpoint_path)
    totals = usage_summary(checkpoint, pricing, gen_model, parse_model)
    return {
        "source_note": ("이 파일은 로컬 체크포인트(.omc/llm_checkpoint/, git 추적 안 됨)에서 "
                        "토큰 총계·호출 수·비용만 집계한 redacted 요약이다 — 프롬프트 원문, "
                        "리뷰 텍스트, evidence는 포함하지 않는다. "
                        "experiments/bench/checkpoint_summary.py로 재생성한다(API 호출 없음, "
                        "로컬 체크포인트 파일이 있어야 함)."),
        "checkpoint_file": checkpoint_path.name,
        "seed": seed, "n_people": n_people, "n_projects": n_projects,
        "review_count": len(checkpoint),
        "pricing_as_of": pricing["as_of"],
        "models": {
            "gen_model": {"name": gen_model, **totals[gen_model]},
            "parse_model": {"name": parse_model, **totals[parse_model]},
        },
        "total_cost_usd": totals["total_cost_usd"],
    }


def main():
    out = build()
    if out is None:
        print("skipped: fixtures/meta.json에 seed/n_people/n_projects/모델명이 없거나 "
              f"로컬 체크포인트 파일이 없다 ({CHECKPOINT_DIR}) — 이미 커밋된 "
              f"{OUT_PATH.relative_to(REPO_ROOT)}는 그대로 둔다.")
        return
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=1), "utf-8")
    print(f"wrote {OUT_PATH}  review_count={out['review_count']}  "
          f"total_cost_usd={out['total_cost_usd']}")


if __name__ == "__main__":
    main()
