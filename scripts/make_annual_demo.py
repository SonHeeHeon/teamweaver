"""연간 계획(12개월) 시연 묶음을 만든다(2026-10-19, claude-b -- 사용자 지시 "계획기간 1년 확장도 하자").

상황 1 "백지에서 1년간의 인력 배치"(2026-10-18 사용자 설명)용. `demo/org-n100`(100명 연초 계획)을 복사해 계획 기간을
2026-10~2027-09(12개월)로 늘린다.
- 사람·이력·평가 글·계획 시작 달은 그대로 -- 리뷰 글 판정 동봉(demo/review_judgments.json)이 그대로 쓰이고 AI를 다시 부르지 않는다.
- 가용률은 사용자 가정(2026-10-19)으로 12개월 전체를 새로 만든다: 대부분 100%, 3%는 12개월 육아휴직(해를 걸침 -- 지난해 8월~올해 8월처럼,
  그 달 0%), 2%는 한 달 휴가(신혼여행·리프레시 -- 사업이 자체 감당하므로 가용률은 그대로, manifest `leave`에만 남긴다). 근거 없는 가정값(NOT_CALIBRATED).
  원본 6개월 묶음의 무작위 가용률(사람·달마다 0%가 10%)은 12개월 사업에 1년 내내 가능한 고급을 18명 중 1명으로 만들어 쓰지 않는다.
  연초 계획 묶음(6개월)·생성기에 같은 가정을 넣는 일은 동시 수행·사업 정원과 함께 재설계한다(claude-a 요청, docs/requests/2026-10-19-availability-redesign.md).
- 대표 사업(J001)은 생성기 규칙대로 기간 끝까지 이어진다. 하반기 신규 사업은 생성기의 사업 규칙(종류·고객·기술·정원·예산)을
  6개월 뒤로 옮겨 같은 수만큼 만든다. 월 최대 수요는 생성기와 같은 85% 상한(하반기 신규 사업만 줄인다 -- 상반기는 원본 그대로).
- 현재 배치는 비운다(백지). 밤 배치는 현재 배치가 없는 데이터에서만 연간 계획(안 A~D)을 미리 푼다.
2026-10-19 claude-a(생성기 v3, b가 넘긴 재설계): 휴직 창·등급별 정원 상한·예산 범위를 생성기 함수(`core.ingest.leave`·`org_profile.shrink_to_supply`·
`budget_of`)로 쓴다 -- 원본 묶음과 같은 규칙·같은 창이 되게. 하반기 신규 사업만 줄이고 상반기·대표 사업은 원본 그대로.

    uv run python scripts/make_annual_demo.py            # demo/org-n100-annual 을 다시 만든다
"""
from __future__ import annotations

import argparse
import random
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SRC = ROOT / "demo" / "org-n100"
DST = ROOT / "demo" / "org-n100-annual"
MONTHS = 12
SEED = 2026 * 7919 + 12            # 하반기 사업용 난수열(원본 생성기 난수열과 섞이지 않는다)
def _month(first: str, k: int) -> str:
    y, m = int(first[:4]), int(first[5:]) - 1 + k
    return f"{y + m // 12:04d}-{m % 12 + 1:02d}"


def make(src: Path = SRC, dst: Path = DST) -> Path:
    from api.workspace_edits import (CURRENT, GRADE_REQS, PROJECTS, SKILL_REQS, read_manifest, read_table, rehash,
                                     write_manifest, write_table)
    from core.ingest.contract import GRADES
    from core.ingest.leave import available_mm, leave_seed, leave_windows, manifest_section
    from core.ingest.org_profile import FLAGSHIP, LLM_ERA, PROJECT_TYPES, budget_of, shrink_to_supply

    files = {p.name: p.read_text("utf-8") for p in sorted(src.iterdir()) if p.is_file()}
    m = read_manifest(files)
    if m.get("horizon_months") != 6 or m.get("scenario"):
        raise SystemExit("원본은 6개월 연초 계획 묶음이어야 한다")
    first = m["horizon_start"]
    months = [_month(first, k) for k in range(MONTHS)]
    rng = random.Random(SEED)

    # 1) 가용률 -- 휴직·휴가 가정(사람 순서 = people.csv 순서)
    _, people = read_table(files, "people.csv")
    leaves = leave_windows([p["person_id"] for p in people], first, leave_seed(int(m["seed"])))
    ah, _ = read_table(files, "availability.csv")
    rows, supply = [], {g: [0.0] * MONTHS for g in GRADES}
    for person in people:
        pid = person["person_id"]
        for k, mo in enumerate(months):
            mm = available_mm(leaves.get(pid), mo)
            rows.append({"person_id": pid, "month": mo, "available_mm": f"{mm:.1f}"})
            if mm >= 1.0:
                supply[person["career_grade"]][k] += 1.0
    write_table(files, "availability.csv", ah, rows)

    # 2) 사업: 대표 사업은 끝까지, 하반기 신규 사업 추가
    ph, projects = read_table(files, PROJECTS)
    gh, grades = read_table(files, GRADE_REQS)
    sh, skills = read_table(files, SKILL_REQS)
    idx = {mo: k for k, mo in enumerate(months)}
    hc_of: dict[str, dict[str, int]] = {}
    for g in grades:
        hc_of.setdefault(g["project_id"], {})[g["career_grade"]] = int(g["headcount"])
    span = {}
    for p in projects:
        if p["project_id"] == FLAGSHIP["project_id"]:
            p["end_month"] = months[-1]
        # 생성기 v4: 계획 전에 시작했거나(장기 사업) 계획 뒤에 끝나는(운영 사업) 사업은 계획 기간으로 자른다 -- 로더와 같은 규칙
        span[p["project_id"]] = (idx.get(p["start_month"], 0 if p["start_month"] < months[0] else MONTHS - 1),
                                 idx.get(p["end_month"], MONTHS - 1 if p["end_month"] > months[-1] else 0))
    used = {p["project_name"] for p in projects}
    life = m.get("lifecycle") or {}
    n_old = sum(1 for p in projects if p["project_id"] != FLAGSHIP["project_id"] and p["project_id"] not in life)
    # ↑ 대표 사업·장기 사업(v4 lifecycle)을 뺀 상반기 사업 수 -- 하반기 신규 사업 수가 v3와 같게
    n_prop = round(n_old * 0.35)
    types = PROJECT_TYPES["DP"]
    new: list[dict] = []
    last_id = max(int(p["project_id"][1:]) for p in projects)
    for k in range(n_old):
        name, sector, clients, pool, _ = rng.choices(types, weights=[t[4] for t in types])[0]
        phase = "제안" if k < n_prop else "실행"
        label = next((f"[{phase}] " + name.format(c=c) for c in rng.sample(clients, len(clients))
                      if f"[제안] {name.format(c=c)}" not in used and f"[실행] {name.format(c=c)}" not in used), None)
        if label is None:
            continue
        used.add(label)
        if phase == "제안":
            start = 6 + rng.randint(0, 4)
            end = start + rng.randint(0, 1)
            hc = {"고급": 1, "중급": rng.randint(1, 2)}
        else:
            start = 6 + rng.randint(0, 2)
            end = min(MONTHS - 1, start + rng.randint(2, 5))
            hc = {"특급": rng.randint(0, 1), "고급": rng.randint(1, 2), "중급": rng.randint(1, 3), "초급": rng.randint(0, 2)}
        last_id += 1
        pid = f"J{last_id:03d}"
        new.append({"project_id": pid, "project_name": label, "sector": sector, "phase": phase,
                    "start_month": months[start], "end_month": months[end]})
        span[pid] = (start, end)
        hc_of[pid] = hc
        for skill in rng.sample(pool, rng.randint(max(2, len(pool) - 2), len(pool))):
            cap = 24 if skill in LLM_ERA else 60
            skills.append({"project_id": pid, "skill_name": skill,
                           "min_experience_months": str(rng.choice([x for x in (12, 24, 36, 60) if x <= cap])),
                           "headcount": str(rng.randint(1, 2))})
    new_ids = {p["project_id"] for p in new}

    # 생성기와 같은 등급·달별 정원 상한(하반기 신규 사업만 줄인다)
    shaped = [{"project_id": pid, "_start": a, "_end": b, "_hc": hc_of[pid], "_flagship": pid not in new_ids}
              for pid, (a, b) in span.items()]
    peak = shrink_to_supply(shaped, supply, MONTHS)
    for p in new:
        p["monthly_budget"] = budget_of(rng, hc_of[p["project_id"]])
        grades += [{"project_id": p["project_id"], "career_grade": g, "headcount": str(k)}
                   for g, k in hc_of[p["project_id"]].items() if k > 0]
    write_table(files, PROJECTS, ph, projects + new)
    write_table(files, GRADE_REQS, gh, grades)
    write_table(files, SKILL_REQS, sh, skills)

    # 3) 백지: 현재 배치 없음
    ch, _ = read_table(files, CURRENT)
    write_table(files, CURRENT, ch, [])

    m["dataset_id"] = "org-n100-s2026-annual"
    m["horizon_months"] = MONTHS
    m["peak_demand_ratio"] = round(peak, 4)
    m["leave"] = manifest_section(leaves)
    m["annual"] = {"source": "org-n100", "new_projects": [p["project_id"] for p in new],
                   "note": "연간 계획 시연(상황 1 · 계획기간 1년): org-n100과 같은 사람·이력·평가 글, 기간 12개월. "
                           "상반기 사업은 원본과 같고 하반기 신규 사업은 생성기 규칙을 별도 난수열로 옮겼다. 가용률은 휴직·휴가 가정(manifest leave)으로 새로 만들었다. "
                           "현재 배치 없음(백지). scripts/make_annual_demo.py가 만든다."}
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
