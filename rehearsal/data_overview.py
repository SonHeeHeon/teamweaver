"""How the synthetic data is made -- a readable overview with counts and sample rows.

    uv run python -m rehearsal.data_overview   ->  rehearsal/results/data-overview.html

Two synthetic sources exist: the frozen demo fixture (fixtures/, core/datagen, 100 people, LLM-written reviews)
and the organisation-shaped CSV bundles of the scale rehearsal (core/ingest/org_profile.py, 100/200/300 people).
Every number and sample on the page is read from those sources at build time.
"""
import collections
import html
import json
import tempfile
from pathlib import Path

from core.config import FIXTURES_DIR
from core.datagen.fixtures_io import load_fixtures
from core.ingest.convert import to_dataset
from core.ingest.loader import load_bundle
from core.ingest.org_profile import generate_org_bundle
from core.ingest.review_text import ORG_REVIEW_ITEMS
from rehearsal.run import RESULTS, SEED

FILE_LABEL = {
    "people.csv": ("사람", "사번(가상)·이름(가상)·경력 등급·개발/컨설팅·소속 그룹·직무"),
    "rate_card.csv": ("단가표", "등급 × 개발/컨설팅별 월 단가(가상 비용 단위)"),
    "person_skills.csv": ("기술 이력", "사람별 기술마다 경력 개월·참여 프로젝트 수·마지막 사용 월"),
    "work_history.csv": ("업무 이력", "과거 프로젝트 투입 기록(시작·종료일, 상태)"),
    "availability.csv": ("월별 가용률", "계획 6개월 동안 달마다 쓸 수 있는 M/M(0~1)"),
    "projects.csv": ("계획 대상 사업", "앞으로 6개월 동안 사람을 배치할 실행·제안 사업, 월 예산"),
    "project_grade_requirements.csv": ("사업별 등급 정원", "사업마다 특급·고급·중급·초급 몇 명"),
    "project_skill_requirements.csv": ("사업별 요구 기술", "사업마다 필요한 기술·최소 경력 개월·필요 인원"),
    "reviews.csv": ("동료 평가", "반기마다 같은 사업을 한 동료가 서로 남긴 평가(좋은 점·아쉬운 점 문장)"),
    "review_items.csv": ("평가 항목", "동료 평가에 체크한 항목(예: 소통·책임감) — 좋은 점/아쉬운 점"),
    "current_assignments.csv": ("현재 투입 명단(선택)", "계획 시작 시점에 이미 실행 사업에 들어가 있는 사람·투입률·잠금(고객 지정 등)"),
    "project_outcomes.csv": ("과거 사업 성과(선택)", "끝난 과거 사업마다 고객 평가 1~5·일정 준수·후속 과제 여부"),
    "replacements.csv": ("인력 교체 기록(선택)", "과거 사업에서 교체된 사람·요청 주체(고객/내부)·사유"),
}


def _e(v) -> str:
    if hasattr(v, "isoformat"):
        v = v.isoformat()[:7] if str(v).endswith("-01") else v.isoformat()
    return html.escape(str(v))


def _table(rows: list[dict], cols: list[str] | None = None, n: int = 3) -> str:
    rows = [{k: v for k, v in r.items() if k != "__row__"} for r in rows[:n]]
    if not rows:
        return "<p>—</p>"
    cols = cols or list(rows[0])
    head = "".join(f"<th>{html.escape(c)}</th>" for c in cols)
    body = "".join("<tr>" + "".join(f"<td>{_e(r.get(c, ''))}</td>" for c in cols) + "</tr>" for r in rows)
    return f"<div class='w'><table><tr>{head}</tr>{body}</table></div>"


def _counter(c: collections.Counter) -> str:
    return " · ".join(f"{html.escape(str(k))} {v}" for k, v in c.most_common())


def build() -> Path:
    work = Path(tempfile.mkdtemp())
    bundles = {}
    for n in (100, 200, 300):
        b, rep = load_bundle(generate_org_bundle(work / f"n{n}", n, seed=SEED))
        ds, parsed = to_dataset(b, rep)
        bundles[n] = (b, ds, rep)
    b300, ds300, rep300 = bundles[300]
    fx, fx_parsed = load_fixtures(FIXTURES_DIR)
    meta = json.loads((FIXTURES_DIR / "meta.json").read_text(encoding="utf-8"))

    count_rows = "".join(
        f"<tr><td><b>{FILE_LABEL[f][0]}</b><br><span class='s'>{f}</span></td>"
        + "".join(f"<td class='n'>{len(bundles[n][0].tables[f]):,}</td>" for n in (100, 200, 300)) + "</tr>"
        for f in FILE_LABEL)
    derived = "".join(
        f"<tr><td>{label}</td>" + "".join(f"<td class='n'>{fn(bundles[n][1]):,}</td>" for n in (100, 200, 300)) + "</tr>"
        for label, fn in (("함께 일한 쌍(협업 기록)", lambda d: len(d.coworks)),
                          ("평가 쌍 수(평가자→피평가자)", lambda d: len({(r.reviewer_id, r.reviewee_id) for r in d.reviews})),
                          ("1인당 평균 기술 수", lambda d: round(sum(len(p.skills) for p in d.people) / len(d.people)))))

    t = b300.tables
    samples = "".join(
        f"<h3>{FILE_LABEL[f][0]} <span class='s'>{f} · {FILE_LABEL[f][1]}</span></h3>{_table(t[f])}"
        for f in FILE_LABEL)
    # a person end-to-end: one AI-group member's history, skills and reviews received
    pid = next(r["person_id"] for r in t["people.csv"] if r["person_id"].startswith("AI"))
    one = {
        "사람": [r for r in t["people.csv"] if r["person_id"] == pid],
        "업무 이력(최근 3건)": sorted([r for r in t["work_history.csv"] if r["person_id"] == pid],
                                 key=lambda r: r["start_date"], reverse=True),
        "기술 이력(경력 긴 순 5개)": sorted([r for r in t["person_skills.csv"] if r["person_id"] == pid],
                                    key=lambda r: -r["experience_months"]),
        "받은 동료 평가(3건)": [r for r in t["reviews.csv"] if r["reviewee_id"] == pid],
    }
    one_html = "".join(f"<h4>{k}</h4>{_table(v, n=5 if '기술' in k else 3)}" for k, v in one.items())

    fx_review = fx.reviews[0]
    fx_parsed0 = fx_parsed[0]
    flag = next(r for r in t["projects.csv"] if r["project_id"] == "J001")
    flag_reqs = [r for r in t["project_skill_requirements.csv"] if r["project_id"] == "J001"]
    flag_grades = [r for r in t["project_grade_requirements.csv"] if r["project_id"] == "J001"]

    doc = f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>가상 데이터 구성</title><style>
:root{{--bg:#fbfaf7;--fg:#1f2328;--muted:#666;--line:#dcd9d2;--acc:#2f6f5e;--box:#eef5f2;--warn:#fff4e0}}
@media (prefers-color-scheme:dark){{:root:not([data-theme="light"]){{--bg:#1b1d1c;--fg:#e8e6e1;--muted:#aaa;--line:#3a3d3b;--acc:#7cc4ad;--box:#24302c;--warn:#3a3222}}}}
body{{background:var(--bg);color:var(--fg);font:15px/1.65 -apple-system,"Apple SD Gothic Neo","Noto Sans KR",sans-serif;margin:0;padding:28px 16px}}
main{{max-width:1000px;margin:auto}}h1{{margin:0 0 .2em}}h2{{color:var(--acc);border-bottom:1px solid currentColor;padding-bottom:.2em;margin-top:2.2em}}
h3{{margin:1.4em 0 .3em;font-size:1.02em}}h4{{margin:1em 0 .2em;font-size:.95em;color:var(--muted)}}
.sub,.s{{color:var(--muted)}}.s{{font-size:.85em;font-weight:400}}
.box{{background:var(--box);border-radius:10px;padding:12px 16px;margin:1em 0}}.warn{{background:var(--warn)}}
table{{border-collapse:collapse;width:100%;font-size:.88em}}td,th{{border-bottom:1px solid var(--line);padding:5px 6px;text-align:left;vertical-align:top}}
th{{color:var(--muted);font-weight:600}}td.n{{text-align:right;font-variant-numeric:tabular-nums}}.w{{overflow-x:auto}}
.flow{{display:flex;flex-wrap:wrap;gap:6px;align-items:center}}.flow span{{background:var(--box);border-radius:8px;padding:5px 9px;font-size:.9em}}.flow b{{color:var(--muted);font-weight:400}}
</style></head><body><main>
<h1>TeamWeaver 가상 데이터는 어떻게 만들어졌나</h1>
<p class="sub">모든 값은 가상이다(실존 인물·고객 아님). 숫자와 샘플은 이 페이지를 만들 때 실제 데이터에서 읽었다 · 생성 시드 {SEED}</p>

<div class="box warn"><b>과거 프로젝트 성과도 실제 시스템처럼 만들었다(2026-10-05).</b> 실제 회사에 있는 세 기록 — <b>고객 평가 · 고객 요청 인력 교체와 사유 · 같은 고객의 후속 과제</b> — 을
<code>project_outcomes.csv</code>, <code>replacements.csv</code>로 흉내 냈다. 성과는 <b>숨은 정답 규칙</b>으로 정했다:
개인의 실제 역량, 팀이 전에 함께 일해 본 비율, 그 업종 경험, 짧게 들락날락한 인원 비율, 고객별 궁합, 운.<br>
<b>겹침을 숨기지 않는다.</b> "전에 함께 일함"은 모델의 협업 점수(함께 일한 개월)와, "업종 경험"은 도메인 기술 경력과 같은 방향의 신호다 —
현실에서도 성과는 보이는 신호와 어느 정도 관련 있으므로 일부러 남겼다. 대신 핵심인 <b>실제 역량은 동료 평가에 일부만 드러나게</b> 했다:
평가는 "보이는 역량"(실제 역량과 상관 약 0.45)과 평가자마다 다른 후함·박함으로 매겨져, 실측 상관이 사람 단위 0.32~0.44, 사업 단위(팀 평가 평균 ↔ 고객 점수) 0.12~0.23이다
(실제 역량 ↔ 고객 점수는 0.45~0.54). 처음 만든 버전은 이 값이 0.97이라 "평가 = 정답"이 됐고, 리뷰에서 지적받아 고쳤다.
모델이 전혀 못 보는 요소는 고객 궁합·들락날락·운이다. 가상 정답이므로 사업 효과는 여전히 <b>NOT_CALIBRATED</b> — 실제 기록을 받으면 같은 자리에 꽂는다.</div>

<h2>1. 데이터는 두 종류다</h2>
<table><tr><th></th><th>① 시연용 고정 데이터</th><th>② 규모 리허설용 조직형 데이터</th></tr>
<tr><td>어디</td><td><code>fixtures/</code> (core/datagen)</td><td><code>core/ingest/org_profile.py</code> → CSV 묶음 → 업로드</td></tr>
<tr><td>규모</td><td>{meta['n_people']}명 · 사업 {meta['n_projects']}개 (고정)</td><td>100명(데이터플랫폼) · 200명(+AI) · 300명(+업무자동화)</td></tr>
<tr><td>동료 평가 문장</td><td><b>LLM이 작성</b>({html.escape(meta.get('gen_model',''))}, 평가 {len(fx.reviews)}건) 후 LLM이 핵심 문장·긍부정 추출</td><td><b>문장 은행으로 조합한 긴 평가</b>(LLM 미사용): 장점 4~5문장·단점 1~2문장, 업무명·업종·기술을 넣어 실제 답변("장점 5줄, 단점 1줄")에 맞춤. 사람마다 고정 강·약점 성향이 있어 여러 동료의 평가가 같은 방향을 가리킴</td></tr>
<tr><td>숙련도</td><td>기술별 레벨 1~5를 직접 뽑음(1인당 평균 {round(sum(len(p.skills) for p in fx.people)/len(fx.people),1)}개)</td><td>실제 시스템처럼 <b>기술별 경력 개월</b> → 레벨로 환산(12/36/60/96개월 경계), 1인당 약 19개</td></tr>
<tr><td>쓰임</td><td>화면 시연·기본 데모·Phase 0/1 실험</td><td>실제 입력 형식(CSV) 검증, 100~300명 성능·품질 측정</td></tr></table>

<h2>2. 조직형 데이터를 만드는 순서 (②)</h2>
<p>앞뒤가 맞도록 <b>업무 이력을 먼저</b> 만들고, 기술 경력·협업·동료 평가를 모두 거기서 뽑는다.</p>
<div class="flow"><span>① 사람 (그룹·직무·등급, 숨은 실제 역량·강약점 성향)</span><b>→</b><span>② 끊김 없는 과거 업무 이력(최근 10년, 입사 시점은 등급별, LLM 계열 기술은 최근 36개월)</span><b>→</b><span>③ 기술 경력 개월 = 그 업무에서 쓴 기술의 기간 합</span><b>→</b><span>④ 함께 일한 쌍 = 같은 과거 사업에 같은 날 있었던 사람들</span><b>→</b><span>⑤ 동료 평가 = 반기마다 같은 사업 동료 3~6명(1인 연 ~10건)</span><b>→</b><span>⑥ 과거 사업 성과·교체 기록(숨은 규칙)</span><b>→</b><span>⑦ 계획 사업·정원·예산·현재 투입 명단</span></div>
<ul>
<li><b>그룹·직무</b>: 데이터플랫폼(데이터 엔지니어·비정형/문서AI·모델러·플랫폼·금융 데이터 컨설턴트), AI(LLM·에이전트·RAG·MLOps·AI 백엔드·AI 컨설턴트), 업무자동화(RPA·워크플로우/BPM·로우코드·문서 자동화·컨설턴트). 300명 직무 분포: {_counter(collections.Counter(r['job_family'] for r in t['people.csv']))}</li>
<li><b>등급</b>(300명): {_counter(collections.Counter(r['career_grade'] for r in t['people.csv']))} · <b>단가</b>: 특급 1600, 고급 1300, 중급 1000, 초급 750(컨설팅 +10%, 가상 단위)</li>
<li><b>계획 사업</b>(300명 기준 60개): {_counter(collections.Counter(r['phase'] for r in t['projects.csv']))} · {_counter(collections.Counter(r['sector'] for r in t['projects.csv']))}.
  고객명은 익명(금융사 가·나…, 계열사 A·B…).</li>
<li><b>최대 사업</b>: {html.escape(flag['project_name'])} — 6개월 실행, 내부 정원 {sum(r['headcount'] for r in flag_grades)}명
  ({', '.join(f"{r['career_grade']} {r['headcount']}" for r in flag_grades)}), 요구 기술 {', '.join(f"{r['skill_name']}({r['min_experience_months']}개월↑·{r['headcount']}명)" for r in flag_reqs)}. 외주 인력은 계산에 넣지 않음.</li>
<li><b>동료 평가 회차</b>(300명): {_counter(collections.Counter(r['review_round'] for r in t['reviews.csv']))}. 좋은 점·아쉬운 점 항목은 각각 1~5개, 같은 20개 목록에서 고름(실제 제도와 동일): {html.escape(', '.join(ORG_REVIEW_ITEMS))}.</li>
<li><b>과거 사업 성과</b>(300명): {len(t['project_outcomes.csv'])}개 사업 — 고객 평가 {_counter(collections.Counter(r['customer_score'] for r in t['project_outcomes.csv']))}점,
  일정 지연 {sum(r['schedule'] == '지연' for r in t['project_outcomes.csv'])}건, 후속 과제 {sum(r['follow_on'] == 'Y' for r in t['project_outcomes.csv'])}건 · 인력 교체 {len(t['replacements.csv'])}건({_counter(collections.Counter(r['requested_by'] for r in t['replacements.csv']))}).</li>
<li><b>월별 가용률</b>: 달마다 1.0(60%)·0.7·0.5·0.3·0(휴가·교육 등 10%) 중 하나.</li>
</ul>

<h2>3. 총 몇 개인가 (②, 시드 {SEED})</h2>
<div class="w"><table><tr><th>파일</th><th>100명</th><th>200명</th><th>300명</th></tr>{count_rows}
<tr><td colspan=4 class="s">변환 후 모델이 쓰는 값</td></tr>{derived}</table></div>
<p class="sub">동료 평가가 많은 이유: 한 사람이 같은 사업을 한 여러 동료에게, 세 번의 반기 평가 때마다 평가받는다. 모델은 같은 쌍의 여러 회차를 방향별로 평균해 쓴다.</p>

<h2>4. 파일별 샘플 (300명, 앞 3행)</h2>{samples}

<h2>5. 한 사람을 따라가 보기 ({html.escape(pid)})</h2>{one_html}

<h2>6. 시연용 고정 데이터(①)의 LLM 동료 평가 예시</h2>
<table><tr><th>평가자 → 피평가자</th><td>{html.escape(fx_review.reviewer_id)} → {html.escape(fx_review.reviewee_id)}</td></tr>
<tr><th>좋은 점 항목</th><td>{html.escape(', '.join(fx_review.positive.items))}</td></tr>
<tr><th>좋은 점 문장(LLM)</th><td>{html.escape(fx_review.positive.text)}</td></tr>
<tr><th>아쉬운 점 항목</th><td>{html.escape(', '.join(fx_review.negative.items))}</td></tr>
<tr><th>아쉬운 점 문장(LLM)</th><td>{html.escape(fx_review.negative.text)}</td></tr>
<tr><th>LLM 분석 결과</th><td>긍부정 {fx_parsed0.text_polarity:+.2f} · 핵심 문장: {html.escape(' / '.join(fx_parsed0.evidence))}</td></tr></table>

<h2>7. 모델에 들어가기 전 변환</h2>
<ul>
<li><b>기술 적합도(S)</b>: 경력 개월 → 레벨 1~5(12개월 미만 1, ~36 2, ~60 3, ~96 4, 그 이상 5) → 사업 요구 레벨 대비 충족도 평균.</li>
<li><b>협업 궁합(C)</b>: 0.4 × 함께 일한 기간(12개월에서 상한) + 0.6 × 동료 평가 점수(항목 균형 50% + 문장 긍부정 50%).</li>
<li><b>협업 보상은 궁합 상위 200쌍만</b>(2026-10-05, 규모 리허설 근거): 이유는 비교 보고서와 <code>docs/model-roadmap.md</code> 참고.</li>
<li><b>설명 근거</b>: 가상 데이터만 동료 평가 원문을 화면·AI에 보낸다. 실제 데이터는 항목 이름만.</li>
</ul>
<p class="sub">생성: <code>uv run python -m rehearsal.data_overview</code> · 데이터 계약 <code>core/ingest/contract.py</code> · 생성기 <code>core/ingest/org_profile.py</code>, <code>core/datagen/</code></p>
</main></body></html>"""
    out = RESULTS / "data-overview.html"
    out.write_text(doc, encoding="utf-8")
    return out


if __name__ == "__main__":
    print(build())
