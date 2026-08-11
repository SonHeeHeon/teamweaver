"""fixture 생성: uv run python scripts/generate_fixtures.py --people 100 --projects 20 \
   --seed 42 --review-mode llm

LLM 모드는 리뷰 1건당 생성 1회 + 파싱 1회, 총 ~2*N회의 순차 API 호출이 필요해
장시간 실행 중 환경에 의해 강제 종료될 수 있다(예: CBC 스윕과 동일 부류의 문제).
이를 견디기 위해 리뷰 단위로 `.omc/llm_checkpoint/`에 즉시 체크포인트를 남기고,
재실행 시 이미 끝난 리뷰는 건너뛴다 -- 죽어도 진행 중이던 리뷰 1건 이상은 잃지
않는다. `--llm-batch-size`로 1회 실행에서 처리할 신규 리뷰 수를 제한해 짧게 끊어
반복 실행할 수 있다(체크포인트가 없으면 재실행 시 전량 미처리로 보고 새로 시작)."""
import argparse
import json
from core.config import FIXTURES_DIR, REPO_ROOT, load_env, load_pricing
from core.datagen.fixtures_io import save_fixtures
from core.datagen.generator import generate_dataset
from core.datagen.llm_checkpoint import (apply_checkpoint, generate_and_parse_checkpointed,
                                         usage_summary)
from core.datagen.parse_reviews import parse_reviews_rule_based

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--people", type=int, required=True)
    ap.add_argument("--projects", type=int, required=True)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--review-mode", choices=["llm", "template"], default="template")
    ap.add_argument("--llm-batch-size", type=int, default=None,
                     help="LLM 모드에서 이번 실행에 처리할 신규 리뷰 수 상한 (기본: 무제한 = 남은 전량)")
    args = ap.parse_args()

    ds = generate_dataset(args.people, args.projects, args.seed)
    if args.review_mode == "llm":
        load_env()
        from openai import OpenAI
        client = OpenAI()
        pricing = load_pricing()
        checkpoint_path = (REPO_ROOT / ".omc" / "llm_checkpoint" /
                            f"seed{args.seed}_p{args.people}_pr{args.projects}.json")
        checkpoint, done, n_processed = generate_and_parse_checkpointed(
            ds, client, pricing["gen_model"], pricing["parse_model"], args.seed,
            checkpoint_path, batch_size=args.llm_batch_size, log_every=25)
        u = usage_summary(checkpoint, pricing, pricing["gen_model"], pricing["parse_model"])
        print(f"usage so far: {json.dumps(u, ensure_ascii=False)}")
        if not done:
            remaining = len(ds.reviews) - len(checkpoint)
            print(f"NOT DONE: processed {n_processed} this run, "
                  f"{len(checkpoint)}/{len(ds.reviews)} total, {remaining} remaining. "
                  f"Re-run the same command to continue from checkpoint "
                  f"({checkpoint_path}).")
            return
        parsed = apply_checkpoint(ds, checkpoint)
        save_fixtures(ds, parsed, FIXTURES_DIR, review_mode=args.review_mode, seed=args.seed,
                      gen_model=pricing["gen_model"], parse_model=pricing["parse_model"])
    else:
        parsed = parse_reviews_rule_based(ds.reviews)
        save_fixtures(ds, parsed, FIXTURES_DIR, review_mode=args.review_mode, seed=args.seed)
    print(f"frozen: {args.people} people / {args.projects} projects / seed {args.seed} "
          f"/ reviews={len(ds.reviews)} mode={args.review_mode}")

if __name__ == "__main__":
    main()
