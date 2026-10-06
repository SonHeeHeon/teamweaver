"""시연용 '일부러 깨진' 데이터 묶음 — 업로드 검증 리포트가 실수를 정확히 짚는 장면(시연 확장 D, 2026-10-06).

    uv run python -m rehearsal.make_broken_demo        # -> demo/org-n100-broken.zip

시연 데이터(demo/org-n100, 실제 시스템 CSV 형식)에 실데이터 반출에서 흔히 생기는 실수 여섯 가지를 넣는다. 데이터 탭에서
올리면 서버가 전환하지 않고 파일·줄·칸을 가리키는 오류 목록을 보여 준다(core/ingest/loader).
"""
import csv
import io
import json
import shutil
import tempfile
import zipfile
from pathlib import Path

DEMO = Path(__file__).resolve().parents[1] / "demo"
OUT = DEMO / "org-n100-broken.zip"
# (파일, 무엇을 망가뜨리나, 시연에서 말할 것)
MISTAKES = [
    ("people.csv", "2번째 사람의 등급 칸을 비운다", "필수 칸 비어 있음"),
    ("availability.csv", "3번째 줄 가용률을 1.5로", "범위 밖 값(0~1)"),
    ("reviews.csv", "1번째 평가의 평가자를 없는 사람(XX9999)으로", "다른 파일에 없는 사람을 가리킴"),
    ("projects.csv", "2번째 사업의 종료 달을 시작 달보다 앞으로", "기간 앞뒤가 바뀜"),
    ("current_assignments.csv", "1번째 현재 배치를 한 번 더 넣는다", "같은 배치 중복"),
    ("person_skills.csv", "1번째 기술 경력 개월에 '열두달'", "숫자 칸에 글자"),
]


def _rows(path: Path) -> tuple[list[str], list[dict]]:
    with path.open(encoding="utf-8", newline="") as f:
        r = csv.DictReader(f)
        return list(r.fieldnames or []), list(r)


def _write(path: Path, fields: list[str], rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def build(src: Path = DEMO / "org-n100") -> bytes:
    tmp = tempfile.TemporaryDirectory(prefix="broken-demo-")
    work = Path(tmp.name) / "b"
    shutil.copytree(src, work)
    m = json.loads((work / "manifest.json").read_text("utf-8"))
    m.pop("files", None)                      # 실제 반출에는 해시가 없다 -- 해시 오류가 다른 실수를 가리지 않게
    m["dataset_id"] = "org-n100-broken"
    (work / "manifest.json").write_text(json.dumps(m, ensure_ascii=False, indent=1), "utf-8")
    f, rows = _rows(work / "people.csv")
    rows[1]["career_grade"] = ""
    _write(work / "people.csv", f, rows)
    f, rows = _rows(work / "availability.csv")
    rows[2]["available_mm"] = "1.5"
    _write(work / "availability.csv", f, rows)
    f, rows = _rows(work / "reviews.csv")
    rows[0]["reviewer_id"] = "XX9999"
    _write(work / "reviews.csv", f, rows)
    f, rows = _rows(work / "projects.csv")
    rows[1]["start_month"], rows[1]["end_month"] = "2027-03", "2026-11"
    _write(work / "projects.csv", f, rows)
    f, rows = _rows(work / "current_assignments.csv")
    rows.insert(1, dict(rows[0]))
    _write(work / "current_assignments.csv", f, rows)
    f, rows = _rows(work / "person_skills.csv")
    rows[0]["experience_months"] = "열두달"
    _write(work / "person_skills.csv", f, rows)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(work.iterdir()):
            info = zipfile.ZipInfo(p.name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, p.read_bytes())
    tmp.cleanup()
    return buf.getvalue()


def main() -> None:
    OUT.write_bytes(build())
    print(f"written {OUT}")


if __name__ == "__main__":
    main()
