"""연말형 운영 중 시연 묶음을 만든다(2026-10-18, claude-b -- 3가지 핵심 상황 설계, 사용자 결정 "연말형 묶음을 따로 만듦").

`demo/org-n100-operating`(100명, 90명 배치·대기 10명·신규 제안 J014·J015)을 복사해, 진행 사업 몇 개가 올해 말(2026-12)에 끝나게 한다.
그 팀원은 "끝나 가는 인력"(2027-01부터 비어 있음)이 되고, 끝나는 사업 하나는 "고도화 과제" 등록의 이전 사업이 된다.
사람·이력·평가 글은 그대로라 리뷰 글 판정 동봉(demo/review_judgments.json)이 그대로 쓰인다 -- AI를 다시 부르지 않는다.
생성기(core/ingest, claude-a)는 고치지 않는다. 미리 계산은 만들지 않는다(운영 중 K 비교 100명 실시간 약 45초, 밤 배치가 채운다).

    uv run python scripts/make_yearend_demo.py            # demo/org-n100-yearend 를 다시 만든다
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SRC = ROOT / "demo" / "org-n100-operating"
DST = ROOT / "demo" / "org-n100-yearend"
# 팀이 작은 사업과 큰 사업을 섞어 끝나게 한다(4·4·8·8명) -- 끝나 가는 인력 24명
ENDING = ("J006", "J009", "J003", "J012")
END_MONTH = "2026-12"


def make(src: Path = SRC, dst: Path = DST, ending=ENDING, end_month: str = END_MONTH) -> Path:
    from api.workspace_edits import PROJECTS, read_manifest, read_table, rehash, write_manifest, write_table
    files = {p.name: p.read_text("utf-8") for p in sorted(src.iterdir()) if p.is_file()}
    h, rows = read_table(files, PROJECTS)
    ids = {r["project_id"] for r in rows}
    missing = [e for e in ending if e not in ids]
    if missing:
        raise SystemExit(f"원본에 없는 사업: {missing}")
    for r in rows:
        if r["project_id"] in ending:
            r["end_month"] = end_month
    write_table(files, PROJECTS, h, rows)
    m = read_manifest(files)
    m["dataset_id"] = "org-n100-s2026-yearend"
    m["pipeline"] = {"J014": {"stage": "제안", "predecessor": None, "registered_at": "2026-10-01"},
                     "J015": {"stage": "예정", "predecessor": None, "registered_at": "2026-10-10"}}
    m["yearend"] = {"ending_projects": list(ending), "end_month": end_month,
                    "note": "연말형 시연(3가지 핵심 상황): 진행 사업 일부가 올해 말에 끝나 그 팀원이 '끝나 가는 인력'이 된다. "
                            "scripts/make_yearend_demo.py가 org-n100-operating에서 만든다 -- 사람·평가 글은 같다."}
    write_manifest(files, m)
    rehash(files)
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True)
    for name, text in files.items():
        (dst / name).write_text(text, "utf-8")
    return dst


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dst", type=Path, default=DST)
    args = ap.parse_args(argv)
    out = make(dst=args.dst)
    print(f"썼다: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
