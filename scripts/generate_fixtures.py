"""fixture 생성: uv run python scripts/generate_fixtures.py --people 100 --projects 20 \
   --seed 42 --review-mode llm"""
import argparse
from core.config import FIXTURES_DIR, load_env, load_pricing
from core.datagen.fixtures_io import save_fixtures
from core.datagen.generator import generate_dataset
from core.datagen.llm_reviews import rewrite_reviews_with_llm
from core.datagen.parse_reviews import parse_reviews_llm, parse_reviews_rule_based

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--people", type=int, required=True)
    ap.add_argument("--projects", type=int, required=True)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--review-mode", choices=["llm", "template"], default="template")
    args = ap.parse_args()

    ds = generate_dataset(args.people, args.projects, args.seed)
    if args.review_mode == "llm":
        load_env()
        from openai import OpenAI
        client = OpenAI()
        pricing = load_pricing()
        ds = rewrite_reviews_with_llm(ds, client, pricing["gen_model"], args.seed)
        parsed = parse_reviews_llm(ds.reviews, client, pricing["parse_model"])
    else:
        parsed = parse_reviews_rule_based(ds.reviews)
    save_fixtures(ds, parsed, FIXTURES_DIR, review_mode=args.review_mode)
    print(f"frozen: {args.people} people / {args.projects} projects / seed {args.seed} "
          f"/ reviews={len(ds.reviews)} mode={args.review_mode}")

if __name__ == "__main__":
    main()
