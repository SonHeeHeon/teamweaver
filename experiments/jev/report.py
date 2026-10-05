"""Jev 실험 결과 → 시연용 HTML(정적, 외부 의존 없음). 문장 속 판정은 수치에서 계산한다(미리 쓴 결론 없음)."""
from __future__ import annotations

import html
from pathlib import Path


def _f(v, digits=3, pct=False):
    if v is None:
        return "—"
    return f"{v * 100:.1f}%" if pct else f"{v:.{digits}f}"


def _row(cells):
    return "<tr>" + "".join(f"<td>{html.escape(str(c))}</td>" for c in cells) + "</tr>"


def _table(head, rows):
    return ("<table><thead><tr>" + "".join(f"<th>{h}</th>" for h in head) + "</tr></thead><tbody>"
            + "".join(_row(r) for r in rows) + "</tbody></table>")


NAMES = {"milp_plan_a": "수학 최적화(현행 솔버, Plan A)", "skill_best": "기술 점수 1등 규칙(우리 점수를 직접 씀 -- 상한에 가까움)",
         "random": "무작위로 고르기(3회 평균)", "jev": "Jev가 고르기", "random_expected": "무작위(기댓값)",
         "llm_fixture": "LLM 파서(gpt-5-nano, 기록값)", "llm_luna": "LLM 파서(현행 gpt-6-luna)"}


def _e1(blocks) -> str:
    parts = []
    for b in blocks:
        ms = b["methods"]
        base = ms["milp_plan_a"]["objective"]
        rows = []
        for k in ("milp_plan_a", "jev", "skill_best", "random"):
            if k not in ms:
                rows.append([NAMES[k], "아직 실행 안 함(키 필요)", "", "", "", ""])
                continue
            m = ms[k]
            ratio = m["objective"] / base if base > 0 and m["objective"] >= 0 else None   # 음수(미충원 감점)면 비율 무의미
            rows.append([NAMES[k], _f(m["objective"]), _f(ratio, pct=True),
                         _f(m.get("unfilled_people"), 0), _f(m.get("violations"), 0),
                         _f(m.get("seconds") or m.get("seconds_wall"), 1) + "초" if (m.get("seconds") or m.get("seconds_wall")) else "—"])
        verdict = ""
        if "jev" in ms:
            j = ms["jev"]
            sb = ms["skill_best"]["objective"]
            verdict = (f"<p class='verdict'>관측(인스턴스당 1회, 반복 없음): Jev가 짠 팀의 배치 점수 <b>{_f(j['objective'])}</b> "
                       f"vs 현행 솔버 {_f(base)}, 기술 1등 규칙 {_f(sb)}, 무작위 {_f(ms['random']['objective'])}. "
                       f"호출 {j['calls']}회, 평균 보기 {j['mean_options']:.1f}개, Jev 응답 시간 합 {j['jev_latency_s']:.1f}초.</p>")
        parts.append(f"<h3>{html.escape(b['instance'])}</h3>"
                     + _table(["방법", "배치 점수", "솔버 대비", "미충원(명)", "규칙 위반", "시간"], rows) + verdict)
    return "".join(parts)


def _e2(e) -> str:
    ms = e["methods"]
    rows = []
    for k in ("jev", "llm_luna", "llm_fixture"):
        m = ms.get(k)
        if not m or "pearson" not in m:
            rows.append([NAMES[k], (m or {}).get("skipped", "아직 실행 안 함(키 필요)"), "", "", "", "", ""])
            continue
        rows.append([NAMES[k], _f(m["pearson"]), _f(m["spearman"]), _f(m["mae"]), _f(m["sign_agreement"], pct=True),
                     _f(m["pair_score_corr"]), _f(m.get("mean_latency_s"), 2) + "초" if m.get("mean_latency_s") else "—"])
    verdict = ""
    if "jev" in ms and "pearson" in ms.get("llm_luna", {}):
        d = ms["jev"]["pearson"] - ms["llm_luna"]["pearson"]
        ci = e.get("delta_pearson_jev_minus_luna_ci95")
        call = ("구분되지 않는다" if ci and ci[0] <= 0 <= ci[1] else "Jev가 높다" if d > 0 else "Jev가 낮다")
        ci_txt = f"95% 구간 [{ci[0]:+.3f}, {ci[1]:+.3f}]" if ci else "구간 없음"
        verdict = (f"<p class='verdict'>관측: 정답과의 상관 차이(Jev − 현행 LLM) <b>{d:+.3f}</b>, {ci_txt} → {call}. "
                   f"응답 시간은 Jev {_f(ms['jev']['mean_latency_s'], 2)}초 vs LLM {_f(ms['llm_luna'].get('mean_latency_s'), 2)}초.</p>")
    return (_table(["방법", "상관(피어슨)", "순위 상관", "평균 오차", "긍/부 일치", "협업 점수 상관", "1건당 시간"], rows)
            + f"<p class='small'>리뷰 {e['n_reviews']}건. 정답은 글을 만들 때 쓴 좋은 점·아쉬운 점 항목 수(글의 의도)다. "
              "판정기는 글만 보고(항목 목록은 안 봄), Jev와 LLM 파서에 같은 기준('서술 전체의 감성 강도')을 준다. "
              "'협업 점수 상관'은 쌍 점수의 절반이 항목 점수라 어느 방법이든 높게 나온다 -- 방법끼리 비교용이다.</p>" + verdict)


def _e3(e) -> str:
    ms = e["methods"]
    rows = []
    for k in ("jev", "skill_best", "random_expected"):
        m = ms.get(k)
        if not m:
            rows.append([NAMES[k], "아직 실행 안 함(키 필요)", "", ""])
            continue
        ci = m.get("top1_ci95")
        top = _f(m["top1"], pct=True) + (f" (95% {ci[0] * 100:.0f}~{ci[1] * 100:.0f}%)" if ci else "")
        rows.append([NAMES[k], top, _f(m["mean_rank"], 2), _f(m.get("mean_regret"))])
    verdict = ""
    if "jev" in ms and ms["jev"].get("top1") is not None:
        verdict = (f"<p class='verdict'>관측: 최선의 교체를 맞힌 비율 Jev <b>{_f(ms['jev']['top1'], pct=True)}</b>, "
                   f"기술 1등 규칙 {_f(ms['skill_best']['top1'], pct=True)}, 무작위 기댓값 {_f(ms['random_expected']['top1'], pct=True)} "
                   f"(n={e['n_cases']}, 구간이 겹치면 차이를 단정할 수 없다).</p>")
    return (_table(["방법", "최선 적중(동점 1등 인정)", "평균 순위(1이 최선)", "최선 대비 점수 손실"], rows)
            + f"<p class='small'>교체 상황 {e['n_cases']}건, 후보 {e['k']}명씩. 후보는 교체해도 규칙 위반이 없는 같은 등급 사람만"
              f"(코드가 먼저 거름). 그래서 그런 후보가 {e['k']}명 이상인 배치만 사례가 된다. 정답은 현행 모델로 다시 평가한 최선이며, "
              f"{e.get('tied_best_cases', 0)}건은 1등이 동점이다 -- 동점이 많아 적중률만으로는 변별력이 약하니 평균 순위·점수 손실을 함께 본다.</p>" + verdict)


CSS = """body{font-family:-apple-system,"Apple SD Gothic Neo","Noto Sans KR",sans-serif;background:#fafaf7;color:#1f2328;margin:0;line-height:1.7}
main{max-width:880px;margin:0 auto;padding:32px 16px 64px}h1{font-size:1.7rem;margin-bottom:.2rem}
h2{font-size:1.25rem;margin-top:2.4rem;border-left:4px solid #0f766e;padding-left:.6rem}h3{font-size:1.02rem}
.sub,.small{color:#6b7280}.small{font-size:.88rem}table{border-collapse:collapse;width:100%;font-size:.93rem;margin:8px 0;background:#fff}
th,td{border-bottom:1px solid #e5e7eb;padding:6px 8px;text-align:left}th{background:#f1f5f9}
.box{background:#fff;border:1px solid #e5e7eb;border-radius:10px;padding:14px 18px;margin:14px 0}
.verdict{background:#ecfdf5;border-radius:8px;padding:8px 12px}.warn{background:#fffbeb;border-color:#fcd34d}"""


def render(result: dict, path: Path) -> Path:
    body = [f"<h1>판단 AI(Jev)는 TeamWeaver의 어디를 대신할 수 있나</h1>",
            f"<p class='sub'>실험 {html.escape(result.get('run_at', ''))} · 합성 데이터 · 사업 효과 NOT_CALIBRATED · "
            f"Jev 입력 {result.get('jev_input_tokens', 0):,} 토큰(약 ${result.get('jev_cost_usd', 0):.4f})"
            + (f" · 기록된 Jev 버전 {', '.join(result['jev_tape']['models'])} "
               f"({result['jev_tape']['recorded_from']} ~ {result['jev_tape']['recorded_to']})"
               if result.get("jev_tape", {}).get("models") else "")
            + ("<br><b>주의: 기록에 Jev 버전이 둘 이상 섞여 있다.</b>" if len(result.get("jev_tape", {}).get("models", [])) > 1 else "")
            + "</p>",
            "<div class='box'><b>Jev란</b>: 글을 쓰지 않고 '보기 중 고르기·점수·맞다/아니다'만 빠르고 싸게 내는 판단 전용 AI"
            "(TypeSafe AI, 2026-09). 만든 회사 문서도 계산·숫자 비교는 코드로 하라고 권한다. 그래서 세 자리에 넣어 봤다: "
            "① 팀을 짜는 계산기(솔버) 자리, ② 점수 공식의 재료(리뷰 글 판정) 자리, ③ 사람의 교체 판단을 돕는 자리.</div>"]
    if "e1" in result:
        body += ["<h2>① 계산기(솔버) 대신 — 빈 자리마다 Jev가 사람을 고르면?</h2>",
                 "<p>모든 방법이 같은 순서로 자리를 채우고, 가용 시간·예산·동시 프로젝트 같은 숫자 조건은 코드가 지킨다. "
                 "달라지는 것은 '누구를 고르는가'뿐이다. 점수는 현행 모델 기준(높을수록 좋음).</p>", _e1(result["e1"])]
    if "e2" in result:
        body += ["<h2>② 점수 재료 대신 — 동료 리뷰 글을 읽고 긍정·부정 판정</h2>",
                 "<p>협업 점수의 절반은 리뷰 글을 읽고 매긴 극성이다. 지금은 LLM이 읽는다. 같은 글을 Jev에게 읽혀 정답과 비교했다.</p>",
                 _e2(result["e2"])]
    if "e3" in result:
        body += ["<h2>③ 판단 보조 — 빠지는 사람 대신 누구를 넣을까</h2>",
                 "<p>교체 검토 화면에서 담당자가 하는 판단이다. 교체해도 규칙 위반이 없는 후보 중 현행 모델 기준 최선을 맞히는지 봤다.</p>",
                 _e3(result["e3"])]
    for part in ("e1", "e2", "e3"):
        if result.get(part + "_incomplete"):
            body.append(f"<p class='verdict'>{part.upper()} 미완: {html.escape(result[part + '_incomplete'])}</p>")
    body.append("<div class='box warn'><b>읽을 때 주의</b>: 데이터는 가상이고 '정답'은 우리 모델(또는 글을 만든 의도) 기준이다. "
                "이 실험은 'Jev가 우리 기준을 얼마나 잘 따라오는가'를 잰 것이지, 실제 회사에서 더 좋은 팀이 나온다는 증명이 아니다. "
                "재현: <code>uv run --group benchmark python -m experiments.jev.run</code> (기록 재생, 키 불필요).</div>")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"<!doctype html><html lang='ko'><head><meta charset='utf-8'>"
                    f"<meta name='viewport' content='width=device-width, initial-scale=1'><title>Jev 대체 실험</title>"
                    f"<style>{CSS}</style></head><body><main>{''.join(body)}</main></body></html>", "utf-8")
    return path
