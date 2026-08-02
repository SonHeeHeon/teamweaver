import json
from datetime import date
from pathlib import Path
from core.domain.models import Dataset, ParsedReview

def _dump(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1), "utf-8")

def save_fixtures(ds: Dataset, parsed: list[ParsedReview] | None, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    d = ds.model_dump(mode="json")
    _dump(out_dir / "people.json", d["people"])
    _dump(out_dir / "projects.json", d["projects"])
    _dump(out_dir / "coworks.json", d["coworks"])
    _dump(out_dir / "reviews_ko.json", d["reviews"])
    _dump(out_dir / "parsed_reviews.json",
          [p.model_dump(mode="json") for p in (parsed or [])])
    _dump(out_dir / "meta.json",
          {"n_people": len(ds.people), "n_projects": len(ds.projects),
           "horizon_start": "2026-09", "frozen_at": date.today().isoformat()})

def load_fixtures(dir: Path) -> tuple[Dataset, list[ParsedReview]]:
    j = lambda n: json.loads((dir / n).read_text("utf-8"))
    ds = Dataset(people=j("people.json"), projects=j("projects.json"),
                 coworks=j("coworks.json"), reviews=j("reviews_ko.json"))
    parsed = [ParsedReview(**p) for p in j("parsed_reviews.json")]
    return ds, parsed
