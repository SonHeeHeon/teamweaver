"""진행 중 사업 기술 경력 늘리기(2026-10-19 사용자 요청, 설계 .omc/plan/2026-10-19-hr-source.md).

사용자: "결국 현재 진행중인 플젝에 대해선 기술 이력은 정적인 데이터가 들어오는거고 이 서비스에서 달이 지나가면 현재 진행중인 등록된 프로젝트에서
등록한 기술에 한해 기술 개월수를 업데이트가 돼야하지않나?" · "Api가 연결돼있고 거기서 … 업데이트를 한다고 하면 우리가 업데이트 안해도 되는데
안한다면 우리가 업데이트 하도록 하자. 그건 설정 화면에서 관리자가 설정".

- 대상: 진행 중(status 진행중) 업무 이력에 등록된 기술(work_skills.csv)만. 늘리는 달은 그 업무 기간 안, 계획 직전 달(계획 시작 달 − 1)까지.
- 어디까지 셌나: 그 기술 경력 행의 last_used_month(원천이 그 달까지 센 경력) -- 그 뒤 달만 더한다. 원천이 정적이라 등록 때 숫자에 멈춰 있어도,
  원천이 이미 갱신했어도 두 번 세지 않는다. last_used_month가 없으면 데이터 기준월(as_of)까지 센 것으로 본다.
  경력 행이 없으면(진행 중 사업에서 처음 쓴 기술) 업무 시작 달부터 센다.
- 데이터 기준월(as_of): manifest `as_of`(YYYY-MM, 인사 시스템에서 받으면 받은 달) → 없으면 기술 경력의 가장 최근 last_used_month.
  계획된 끝 달이 기준월보다 앞인데 아직 진행중이면(연장) 계획 직전 달까지 이어진 것으로 본다(기준월에 끝나는 업무는 계획대로 끝).
- 갱신 주체: manifest `skill_months_owner` = "service"(기본, 이 서비스가 늘림) | "source"(원천 시스템이 이미 갱신 -- 늘리지 않음).
- 계획 기간 안에서는 달마다 늘리지 않는다(계획 시작 시점의 숙련도 하나 -- 달별 숙련도는 모델 변경이라 범위 밖).
로더가 검증 뒤 person_skills 행을 고쳐 계산(to_dataset)·지식 그래프·실험 도구가 같은 경력을 본다. 원문 CSV는 바꾸지 않는다.
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict

# 계산 버전(api/datasets.rules_version)에 들어간다 -- 늘리는 규칙을 바꾸면 같은 원천이라도 캐시·미리 계산이 옛 경력으로 쓰이지 않게 바꾼다
AGING_RULES = "2026-10-19:ongoing-work-skills-after-last-used"
OWNERS = ("service", "source")
ONGOING = "진행중"


def _month(d: dt.date) -> dt.date:
    return dt.date(d.year, d.month, 1)


def _add(d: dt.date, k: int) -> dt.date:
    y, m = divmod(d.month - 1 + k, 12)
    return dt.date(d.year + y, m + 1, 1)


def owner_of(manifest: dict | None) -> str:
    v = (manifest or {}).get("skill_months_owner")
    return v if v in OWNERS else "service"


def data_as_of(manifest: dict, tables: dict) -> tuple[dt.date | None, str]:
    """(데이터 기준월, 어디서 정했나). manifest as_of는 로더가 형식을 검사한 뒤에 부른다."""
    raw = manifest.get("as_of")
    if isinstance(raw, str):
        y, m = raw.split("-")
        return dt.date(int(y), int(m), 1), "manifest as_of"
    lasts = [r["last_used_month"] for r in tables.get("person_skills.csv") or [] if r.get("last_used_month")]
    if lasts:
        return _month(max(lasts)), "기술 경력의 가장 최근 사용 달"
    return None, "없음"


def age_ongoing_skills(manifest: dict, tables: dict, horizon: list[dt.date], report, canonical=None) -> dict:
    """person_skills.csv 행을 제자리에서 고친다. canonical: 기술 이름 → 대표 이름(별칭을 같은 기술로 맞춘다, 없으면 이름 그대로).
    돌려주는 값 = 기록(manifest·화면용): {owner, as_of, as_of_from, cutoff, people, skills, max_months}."""
    canon = canonical or (lambda name: name)
    owner = owner_of(manifest)
    as_of, how = data_as_of(manifest, tables)
    out = {"owner": owner, "as_of": as_of.strftime("%Y-%m") if as_of else None, "as_of_from": how,
           "cutoff": None, "people": 0, "skills": 0, "max_months": 0,
           "skipped": None}                              # "no_work_skills" = 업무별 사용 기술 표가 없어 늘리지 못함(화면이 "최신"과 구분)
    if not horizon:
        return out
    cutoff = _add(horizon[0], -1)
    out["cutoff"] = cutoff.strftime("%Y-%m")
    ongoing = {w["work_id"]: w for w in tables.get("work_history.csv") or [] if w.get("status") == ONGOING}
    if not ongoing:
        return out
    if owner == "source":
        report.notes.append("진행 중 사업 기술 경력은 원천 시스템이 갱신한다(설정) -- 이 서비스는 늘리지 않았다")
        return out
    links = tables.get("work_skills.csv") or []
    if not links:
        # 데이터가 계획 직전 달보다 오래됐으면 빠진 달이 반드시 있다 -- 경고. 아니면(원천이 그 달까지 셌다고 보면) 알림만
        msg = (f"진행 중 업무 {len(ongoing)}건에 등록된 기술을 알 수 없어(업무별 사용 기술 표 없음) 계획 직전 {cutoff:%Y-%m}까지의 "
               f"경력을 늘리지 않았다")
        if as_of is not None and as_of < cutoff:
            report.warn("work_skills.csv", f"{msg} -- 데이터 기준월 {as_of:%Y-%m} 뒤의 달이 빠진다")
        else:
            report.notes.append(msg + "(원천이 계획 직전 달까지 셌다고 본다)")
        out["skipped"] = "no_work_skills"
        return out
    rows = tables.setdefault("person_skills.csv", [])
    index: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in rows:
        index[(r["person_id"], canon(r["skill_name"]))].append(r)
    by_work: dict[str, list[str]] = defaultdict(list)
    for x in links:
        by_work[x["work_id"]].append(x["skill_name"])
    live: dict[tuple[str, str], set] = defaultdict(set)           # (사람, 대표 기술) → 진행 중 업무 달(업무 시작 ~ 끝, 겹쳐도 한 번만)
    raw_name: dict[tuple[str, str], str] = {}
    for wid, w in ongoing.items():
        end_m = _month(w["end_date"])
        end = cutoff if as_of is not None and end_m < as_of else min(end_m, cutoff)    # 끝 달이 지났는데 진행중 = 연장
        for sk in by_work.get(wid, []):
            key = (w["person_id"], canon(sk))
            m = _month(w["start_date"])
            while m <= end:
                live[key].add(m)
                m = _add(m, 1)
            raw_name.setdefault(key, sk)
    added: dict[tuple[str, str], int] = {}
    for key, span in sorted(live.items()):
        hits = index.get(key)
        if hits:
            # 행마다 그 행이 센 마지막 달 뒤만 더한다 -- 별칭 행(Spark·Apache Spark)이 서로 다른 달까지 셌으면 각자 따라잡고, 합칠 때 가장 긴 경력을 쓴다
            n = 0
            for r in hits:
                done = _month(r["last_used_month"]) if r.get("last_used_month") else as_of
                more = sorted(m for m in span if done is None or m > done)
                if more:
                    r["experience_months"] = int(r["experience_months"]) + len(more)
                    r["last_used_month"] = max(r.get("last_used_month") or more[-1], more[-1])
                    n = max(n, len(more))
            if n:
                added[key] = n
        elif span:                                                 # 진행 중 사업에서 처음 쓴 기술 -- 새 경력 행(업무 시작 달부터)
            rows.append({"person_id": key[0], "skill_name": raw_name[key], "skill_category": None, "project_count": 1,
                         "experience_months": len(span), "last_used_month": max(span), "__row__": None})
            added[key] = len(span)
    if not added:
        return out
    people = {k[0] for k in added}
    out.update(people=len(people), skills=len(added), max_months=max(added.values()))
    report.notes.append(f"진행 중 사업 기술 경력을 늘렸다(이 서비스가 갱신 -- 설정): 각 경력의 마지막 사용 달 뒤 계획 직전 {cutoff:%Y-%m}까지, "
                        f"{len(people)}명 {len(added)}건, 최대 +{out['max_months']}개월")
    return out
