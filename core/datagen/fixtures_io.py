import json
from datetime import date
from pathlib import Path
from core.domain.models import Dataset, ParsedReview

def _dump(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1), "utf-8")

def save_fixtures(ds: Dataset, parsed: list[ParsedReview] | None, out_dir: Path,
                  review_mode: str = "template", seed: int | None = None,
                  gen_model: str | None = None, parse_model: str | None = None) -> None:
    """`seed`/`gen_model`/`parse_model`은 선택 인자다(최종 리뷰 Important 13) --
    프로비넌스(어느 시드로, 어느 모델로 만들었는지)를 meta.json에 기록해 두면
    experiments/report.py가 "세 실험 공통 시드"를 손으로 타이핑하지 않고
    커밋된 이 파일에서 직접 확인할 수 있다. review_mode="template"일 때는
    LLM을 안 쓰므로 gen_model/parse_model은 보통 None으로 둔다."""
    out_dir.mkdir(parents=True, exist_ok=True)
    d = ds.model_dump(mode="json")
    _dump(out_dir / "people.json", d["people"])
    _dump(out_dir / "projects.json", d["projects"])
    _dump(out_dir / "coworks.json", d["coworks"])
    _dump(out_dir / "reviews_ko.json", d["reviews"])
    _dump(out_dir / "parsed_reviews.json",
          [p.model_dump(mode="json") for p in (parsed or [])])
    meta = {"n_people": len(ds.people), "n_projects": len(ds.projects),
            "horizon_start": "2026-09", "frozen_at": date.today().isoformat(),
            "review_mode": review_mode}
    if seed is not None:
        meta["seed"] = seed
    if gen_model is not None:
        meta["gen_model"] = gen_model
    if parse_model is not None:
        meta["parse_model"] = parse_model
    _dump(out_dir / "meta.json", meta)

def load_fixtures(dir: Path) -> tuple[Dataset, list[ParsedReview]]:
    j = lambda n: json.loads((dir / n).read_text("utf-8"))
    ds = Dataset(people=j("people.json"), projects=j("projects.json"),
                 coworks=j("coworks.json"), reviews=j("reviews_ko.json"))
    parsed = [ParsedReview(**p) for p in j("parsed_reviews.json")]
    return ds, parsed
