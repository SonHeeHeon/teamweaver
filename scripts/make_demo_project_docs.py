"""시연용 가상 발주 자료 만들기(2026-10-23 사용자 "기존 사업에서도 그런 자료가 사전에 등록했다는 가정하에 등록이 몇개는 돼있고", claude-b).

연말형·운영 중 시연 묶음(같은 사업 번호·이름·요구)의 사업 몇 개에 발주 자료 PDF를 만든다. 내용은 그 묶음의 사업 표·요구 기술·등급 정원에서
가져오고, 모든 쪽 머리에 "가상 자료"를 적는다(실제 발주처·공고와 관계없음). 결과: demo/project_docs/{index.json, *.pdf}.
서버(api/project_docs)는 가상 데이터이고 데이터 id가 맞을 때만 이 자료를 보인다.

    uv run python scripts/make_demo_project_docs.py      # Playwright chromium 필요(uv run playwright install chromium)

요구 기술·사업 기간을 바꾸는 생성기 변경이 있으면 다시 돌린다(시험 tests/test_demo_project_docs.py가 어긋나면 실패한다).
"""
from __future__ import annotations

import csv
import hashlib
import html
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCES = ["org-n100-operating", "org-n100-yearend"]          # 같은 사업 번호 -- 두 묶음 모두에 보인다
OUT = ROOT / "demo" / "project_docs"
# (사업, 종류) -- 진행 사업 3개(계약 뒤 과업지시서·원래 RFP), 신규 사업 3개(단계에 맞는 자료, J020은 사전규격 → RFP 둘)
DOCS = [("J001", "과업지시서"), ("J003", "과업지시서"), ("J011", "제안요청서(RFP)"),
        ("J018", "제안요청서(RFP)"), ("J019", "사업 개요"), ("J020", "사전규격"), ("J020", "제안요청서(RFP)")]
SLUG = {"사전규격": "prespec", "제안요청서(RFP)": "rfp", "과업지시서": "sow", "사업 개요": "brief"}
SECTOR = {"대외공공": ("공공기관", "나라장터 공고 · 협상에 의한 계약"),
          "대외금융": ("금융사", "초청 업체 대상 제안 요청 · 비밀유지 서약"),
          "대내": ("그룹 계열사", "그룹 내부 발주")}
# 사업 이름의 낱말로 고르는 배경·과업 범위(가상 문장)
THEMES = [
    ("AI 에이전트", "고객 상담·내부 업무 문서를 찾아 답하는 AI 에이전트를 만들고, 그 아래 데이터 플랫폼(수집·정제·메타데이터)을 함께 구축한다.",
     ["비정형 문서 수집과 OCR 변환", "문서 메타데이터 표준·카탈로그 수립", "적재 파이프라인(ETL) 구축", "에이전트 답변 품질 검증 체계",
      "금융 업무 규정(개인정보·보존 기한) 반영"]),
    ("클라우드 마이그레이션", "운영 중인 데이터 개방 플랫폼을 기관 클라우드로 옮기고 일부 기능을 수정한다(본 사업 운영 계약에 붙는 추가 과제).",
     ["서버·데이터베이스의 사설 클라우드(Private Cloud) 이전", "컨테이너(Kubernetes) 운영 환경 전환", "개방 통계 보고서(BI) 화면 수정",
      "이전 뒤 성능·보안 점검"]),
    ("데이터 개방 플랫폼", "공공데이터 개방 포털을 안정적으로 운영하고, 개방 데이터셋의 품질과 이용 통계를 관리한다.",
     ["개방 데이터셋 정비·갱신", "개방 API 운영과 장애 대응", "데이터 품질 진단(SQL)", "이용 통계 보고서(BI) 작성", "공공 업무 지침 반영"]),
    ("메타데이터 전환", "쌓여 있는 문서 이미지를 글자로 바꾸고 메타데이터를 붙여 새 문서 관리 체계로 옮긴다.",
     ["문서 이미지 OCR 변환", "메타데이터 추출·표준화", "기존 문서 관리 시스템 이관(ETL)", "금융 업무 분류 체계 적용"]),
    ("데이터 플랫폼 고도화", "온프레미스 데이터 플랫폼을 사내 클라우드·쿠버네티스로 옮기고 배치·스트리밍·대용량 처리 체계를 정비한다.",
     ["사설 클라우드(Private Cloud) 기반 구성", "쿠버네티스 운영 체계", "배치 작업(Airflow) 재설계", "스트리밍(Kafka) 수집", "대용량 처리(Spark) 최적화"]),
]
GRADES = ["특급", "고급", "중급", "초급"]


def _read(name: str, table: str) -> list[dict]:
    with open(ROOT / "demo" / name / table, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _project(name: str, pid: str) -> dict:
    proj = next(r for r in _read(name, "projects.csv") if r["project_id"] == pid)
    skills = [r for r in _read(name, "project_skill_requirements.csv") if r["project_id"] == pid]
    grades = {r["career_grade"]: int(r["headcount"]) for r in _read(name, "project_grade_requirements.csv") if r["project_id"] == pid}
    return {"row": proj, "skills": skills, "grades": grades}


def _months(a: str, b: str) -> int:
    (ya, ma), (yb, mb) = map(int, a.split("-")), map(int, b.split("-"))
    return (yb - ya) * 12 + (mb - ma) + 1


def _shift(ym: str, k: int) -> str:
    y, m = map(int, ym.split("-"))
    n = y * 12 + (m - 1) + k
    return f"{n // 12}-{n % 12 + 1:02d}"


def _years(months: int) -> str:
    y, m = divmod(months, 12)
    return f"{y}년" + (f" {m}개월" if m else "") if y else f"{m}개월"


# 사업 예산은 원으로 적는다(2026-10-23 사용자 "1억에 6개월 프로젝트면 1억÷6개월 하면 월 비용 나오지않나?") -- 가상 데이터 단가표(특급 1,600·고급 1,300·…점/월)는
# 대략 만 원 단위라 1점 = 1만 원으로 본다. 자료에서 사업 정보 채우기(api/rag/doc_brief)가 총액 ÷ 개월로 원래 월 예산을 되찾는지 잴 수 있다.
WON_PER_POINT = 10_000


def _theme(name: str):
    return next((t for t in THEMES if t[0] in name),
                ("", "업무 데이터를 모으고 정리해 필요한 사람이 쓸 수 있게 한다.", ["데이터 수집·정제", "운영 지원"]))


def _html(pid: str, kind: str, p: dict) -> str:
    r = p["row"]
    e = html.escape
    client, channel = SECTOR.get(r["sector"], ("발주처", "발주"))
    months = _months(r["start_month"], r["end_month"])
    budget = int(r["monthly_budget"])
    _, background, scope = _theme(r["project_name"])
    skill_rows = "".join(f"<tr><td>{e(s['skill_name'])}</td><td>{_years(int(s['min_experience_months']))} 이상</td>"
                         f"<td>{s['headcount']}명</td></tr>" for s in p["skills"])
    grade_line = " · ".join(f"{g} {p['grades'][g]}명" for g in GRADES if g in p["grades"])
    prose = ", ".join(f"{s['skill_name']} 경력 {_years(int(s['min_experience_months']))} 이상 {s['headcount']}명" for s in p["skills"])
    title = {"사전규격": "사전규격 공개(안)", "제안요청서(RFP)": "제안요청서(RFP)", "과업지시서": "과업지시서", "사업 개요": "사업 개요(영업 내부 정리)"}[kind]
    start = r["start_month"]
    sections = [
        ("사업 개요", f"""<table class="kv">
<tr><th>사업명</th><td>{e(r['project_name'])}</td></tr>
<tr><th>발주처</th><td>{e(client)}(가상) · {e(channel)}</td></tr>
<tr><th>사업 기간</th><td>{e(r['start_month'])} ~ {e(r['end_month'])} ({months}개월)</td></tr>
<tr><th>사업 예산</th><td>총 {budget * months * WON_PER_POINT:,}원(부가세 포함) · 월 {budget:,}만 원</td></tr>
</table>"""),
        ("추진 배경과 목적", f"<p>{e(background)}</p>"),
        ("과업 범위", "<ul>" + "".join(f"<li>{e(x)}</li>" for x in scope) + "</ul>"),
        ("투입 인력과 기술 요건", f"""<p>투입 인력은 {e(grade_line)}을 기준으로 한다. 수행사는 다음 기술 경력을 갖춘 인력을 투입한다: {e(prose)}.</p>
<table class="grid"><tr><th>기술</th><th>최소 경력</th><th>인원</th></tr>{skill_rows}</table>"""),
    ]
    if kind == "사전규격":
        sections.append(("의견 제출", f"<p>이 문서는 입찰공고 전 업체 의견을 받기 위한 초안이다. 공개일({_shift(start, -3)}) 다음 날부터 5일 안에 규격 담당자에게 의견을 낸다. "
                                        "의견을 반영해 제안요청서를 확정한다.</p>"))
    if kind == "제안요청서(RFP)":
        tech = "90" if r["sector"] == "대외공공" else "80"
        sections.append(("제안 일정과 평가", f"""<table class="kv">
<tr><th>입찰공고</th><td>{_shift(start, -2)}</td></tr><tr><th>제안서 마감</th><td>{_shift(start, -1)}</td></tr>
<tr><th>평가 배점(가상)</th><td>기술 {tech} · 가격 {100 - int(tech)}</td></tr>
<tr><th>제출 서류</th><td>제안서, 투입 인력 이력, 수행 계획</td></tr></table>"""))
    if kind == "과업지시서":
        sections.append(("산출물과 보고", "<ul><li>착수 보고서 · 월간 진척 보고</li><li>설계서 · 시험 결과서</li><li>운영 지침 · 완료 보고서</li></ul>"))
    if kind == "사업 개요":
        sections.append(("영업 메모", f"<p>고객사 담당자 면담으로 파악한 내용이다. 정식 제안요청서는 {_shift(start, -2)}쯤 받을 예정이다. "
                                     "필요 기술은 면담 내용으로 추정한 것이라 바뀔 수 있다.</p>"))
    body = "".join(f"<h2>{i}. {e(h)}</h2>{c}" for i, (h, c) in enumerate(sections, 1))
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><style>
@page {{ size: A4; margin: 18mm 16mm 16mm; }}
body {{ font-family: "Apple SD Gothic Neo", "Noto Sans KR", "Malgun Gothic", sans-serif; font-size: 10.5pt; line-height: 1.6; color: #1f2933; }}
.fake {{ border: 2px solid #b91c1c; color: #b91c1c; padding: 6px 10px; font-weight: 700; margin-bottom: 14px; }}
h1 {{ font-size: 17pt; margin: 0 0 2px; }} .sub {{ color: #64748b; margin: 0 0 14px; }}
h2 {{ font-size: 12pt; margin: 16px 0 6px; border-bottom: 1px solid #cbd5e1; padding-bottom: 3px; }}
table {{ border-collapse: collapse; width: 100%; }} th, td {{ border: 1px solid #cbd5e1; padding: 4px 8px; text-align: left; }}
table.kv th {{ width: 7.5em; background: #f8fafc; }} table.grid th {{ background: #f1f5f9; }}
</style></head><body>
<div class="fake">가상 자료 — TeamWeaver 시연용으로 만든 문서입니다. 실제 발주처·사업·공고와 관계없습니다.</div>
<h1>{e(title)}</h1><p class="sub">{e(pid)} · {e(r['project_name'])}</p>
{body}
</body></html>"""


def main() -> int:
    from playwright.sync_api import sync_playwright
    primary, others = SOURCES[0], SOURCES[1:]
    manifests = {s: json.loads((ROOT / "demo" / s / "manifest.json").read_text(encoding="utf-8")) for s in SOURCES}
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.pdf"):
        old.unlink()
    entries = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        for pid, kind in DOCS:
            p = _project(primary, pid)
            for o in others:     # 두 묶음에서 같은 사업이어야 한 자료를 함께 보인다
                q = _project(o, pid)
                if (q["row"], q["skills"], q["grades"]) != (p["row"], p["skills"], p["grades"]):
                    print(f"{pid}: {primary}와 {o}의 사업 내용이 다르다 -- DOCS에서 빼거나 묶음별로 만든다", file=sys.stderr)
                    return 1
            page.set_content(_html(pid, kind, p), wait_until="load")
            pdf = page.pdf(format="A4", print_background=True, prefer_css_page_size=True)
            fname = f"{pid.lower()}-{SLUG[kind]}.pdf"
            (OUT / fname).write_bytes(pdf)
            # 데이터마다 그 사업의 등록 시각(manifest pipeline registered_at, 없으면 null) -- 서버는 지금 등록 시각과 같을 때만 보인다
            # (번호를 다시 쓴 새 사업에 가상 자료가 붙지 않게, 폴백 리뷰 M1)
            reg = {m["dataset_id"]: ((m.get("pipeline") or {}).get(pid) or {}).get("registered_at") for m in manifests.values()}
            entries.append({"id": f"{pid.lower()}-{SLUG[kind]}", "registered_at": reg, "project_id": pid, "kind": kind,
                            "filename": f"{pid} {kind} (가상).pdf", "file": fname, "size": len(pdf),
                            "sha256": hashlib.sha256(pdf).hexdigest()})
            print(f"{fname}: {len(pdf):,} bytes")
        browser.close()
    index = {"note": "가상 자료 -- TeamWeaver 시연용. 실제 발주처·사업·공고와 관계없다.",
             "generator": "scripts/make_demo_project_docs.py", "sources": SOURCES, "docs": entries}
    (OUT / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
