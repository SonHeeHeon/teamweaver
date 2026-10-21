"""낯선 기술 이름 정리 시연 묶음(2026-10-22, claude-a -- core/ingest/skill_mapping): 연초 100명 묶음(`demo/org-n100`)의 기술 이름 일부를
사전에 **없는** 표기(오타·한글 음차·꼬리말)로 바꾼 사본. 여러 원천을 합친 인사 자료에 흔한 모양이다. 사람·평가 글은 그대로라 판정 동봉이 그대로 쓰인다.

바꾸는 규칙(결정적): 아래 표의 개념마다 그 이름을 가진 사람 행을 사람 번호 순으로 세 명 중 한 명꼴로 바꾸고, 사업 요구 행도 표에 있으면 하나 바꾼다.
표의 표기는 실행할 때 사전으로 다시 확인해 사전이 알아보는 것은 빼고 알린다(사전이 바뀌어도 "낯선 이름"만 남게).

  uv run python scripts/make_messy_skill_demo.py [출력 폴더]      # 기본 demo/org-n100-messy-skills (+ 같은 이름의 .zip -- 화면에서 올리기용)
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SRC = ROOT / "demo" / "org-n100"
OUT = ROOT / "demo" / "org-n100-messy-skills"
# 개념 대표 이름 → 바꿔 쓸 낯선 표기(정답 개념은 이 표 자체 -- 측정·시험이 같이 쓴다)
VARIANTS = {
    "Kubernetes": "Kuberentes", "Kafka": "Apache Kafak", "Python": "파이선", "Docker": "Dokcer", "Spark": "Apache Sparks",
    "Hadoop": "하둡 에코시스템", "Scala": "스칼라 언어", "ETL": "이티엘", "Spring": "스프링 프레임웍", "React": "리엑트",
    "SQL": "SQL 쿼리 작성", "Java": "자바 개발", "FastAPI": "Fast API 서버", "데이터 모델링": "데이타 모델링",
}
REQUIREMENT_VARIANTS = {"ETL": "이티엘", "Spark": "Apache Sparks"}
EVERY = 3


def main(out: Path = OUT) -> dict:
    from api.workspace_edits import read_manifest, read_table, rehash, write_manifest, write_table
    from core.ingest.skills import load_dictionary
    sd = load_dictionary()
    variants = {k: v for k, v in VARIANTS.items() if sd.lookup(v) is None}
    skipped = sorted(set(VARIANTS) - set(variants))
    files = {p.name: p.read_text("utf-8") for p in SRC.iterdir() if p.is_file()}
    h, rows = read_table(files, "person_skills.csv")
    seen: dict[str, int] = {}
    changed = 0
    for r in sorted(rows, key=lambda x: (x["person_id"], x["skill_name"])):
        v = variants.get(r["skill_name"])
        if v is None:
            continue
        seen[r["skill_name"]] = seen.get(r["skill_name"], 0) + 1
        if seen[r["skill_name"]] % EVERY == 1:
            r["skill_name"] = v
            changed += 1
    write_table(files, "person_skills.csv", h, rows)
    h, reqs = read_table(files, "project_skill_requirements.csv")
    req_changed = 0
    for name, v in REQUIREMENT_VARIANTS.items():
        row = next((r for r in sorted(reqs, key=lambda x: x["project_id"]) if r["skill_name"] == name), None)
        if row is not None and v in variants.values():
            row["skill_name"] = v
            req_changed += 1
    write_table(files, "project_skill_requirements.csv", h, reqs)
    m = read_manifest(files)
    m["dataset_id"] = f"{m.get('dataset_id', 'org-n100')}-messy-skills"
    m["description"] = "연초 100명 묶음의 기술 이름 일부를 사전에 없는 표기로 바꾼 사본(기술 이름 정리 시연, scripts/make_messy_skill_demo.py)"
    m["messy_skill_names"] = {"variants": variants, "requirement_variants": REQUIREMENT_VARIANTS, "every": EVERY}
    write_manifest(files, m)
    rehash(files)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    for name, text in files.items():
        (out / name).write_text(text, "utf-8")
    # 화면에서 올리는 zip(시연 D1 -- 데이터 탭 "인력·사업 데이터 올리기"): 같은 파일들. 가상 데이터라 평가 글 판정은 동봉 값을 쓴다(AI 다시 안 부름)
    import zipfile
    zpath = out.with_suffix(".zip")
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for name in sorted(files):                       # 시각을 고정해 다시 만들어도 같은 바이트
            z.writestr(zipfile.ZipInfo(name, date_time=(2026, 10, 22, 0, 0, 0)), files[name], compress_type=zipfile.ZIP_DEFLATED)
    info = {"out": str(out.relative_to(ROOT)) if out.is_relative_to(ROOT) else str(out), "person_rows_changed": changed,
            "requirement_rows_changed": req_changed, "variants": len(variants), "skipped_known": skipped}
    print(json.dumps(info, ensure_ascii=False))
    return info


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else OUT)
