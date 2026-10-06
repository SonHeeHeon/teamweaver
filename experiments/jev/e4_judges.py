"""E4. 리뷰 글 판정기 비교: LLM(OpenAI 호환, gpt-6-luna) vs Jev -- 정확도·비용·소요 시간.

왜: 규칙 기반 판정은 글 극성 자리에 항목 균형을 다시 넣어, 평가 사유(글)를 점수에 전혀 쓰지 않았다
(쌍 리뷰 점수 = 0.5×항목 + 0.5×글 극성 → 사실상 항목 100%). 글을 읽는 판정기로 바꾸려고 두 후보를 비교했다.
결정(사용자 2026-10-06): 선택지 없이 LLM으로 통일. 이 실험이 그 근거이며 최종 보고에 쓴다.

시험
- A. 시연 묶음(demo/org-n100) 리뷰 원문 1,372건. 정답이 없다(생성기가 아쉬운 점을 한 줄로 몰아 쓰고
  "보완하면 좋겠습니다"처럼 부드럽게 써서, 글이 항목만큼 부정적이지 않다). 분포·두 판정기 일치도만 본다.
- B. 같은 리뷰에서 300건을 뽑아(seed 7), 고른 항목을 **하나도 빠짐없이 한 문장씩** 다시 쓴 글. 문장은
  claude-a 생성기의 문장 은행(core/ingest/review_text.POSITIVE/NEGATIVE)을 읽기만 해서 쓴다. 칭찬 첫 문장·
  몰아쓰기가 없어 글이 항목을 그대로 담으므로, 정답 = 항목 균형 (좋은 점 수 − 아쉬운 점 수)/전체.
- P. 손으로 쓴 대조 문장 4건(명백한 부정·명백한 긍정·시연 원문·같은 항목을 문장으로 푼 글) -- 척도 점검.

판정기
- llm: `core/datagen/parse_reviews._SYSTEM`과 같은 지시문(서비스 판정과 같은 조건), JSON 응답의 text_polarity.
- jev: Score 5단계(0=매우 부정 … 4=매우 긍정) → 등급 확률 기댓값 → [-1, 1] (E2와 같은 지시문).

기록: 판정값·건당 지연·토큰 사용량을 `experiments/jev/results/e4_<판정기>_<시험>.json`에 남기고, 있으면 다시
부르지 않는다. 비용 = 토큰 × 공식 단가(core/config pricing.json의 gpt-6-luna, Jev 입력 $0.042/100만·출력 무료).
보고서: `outputs/review-judge-comparison.html`.

실행: `uv run --group benchmark python -m experiments.jev.e4_judges` (OPENAI_API_KEY·TYPESAFE_API_KEY 필요,
기록이 있으면 키 없이 보고서만 다시 만든다).
사내 LLM으로 다시 재기: `E4_TAG=onprem TEAMWEAVER_REVIEW_BASE_URL=<사내 주소> TEAMWEAVER_REVIEW_MODEL=<모델>
TEAMWEAVER_REVIEW_API_KEY=<있으면> uv run ... -m experiments.jev.e4_judges` -- 결과 파일·보고서 이름에 태그가 붙어
기존 OpenAI 기록을 재사용하지 않는다(서비스 판정기와 같은 주소·모델·키 규칙, api/review_judge.py)."""
from __future__ import annotations

import html
import json
import os
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import numpy as np

from core.config import REPO_ROOT, load_env, load_pricing
from core.datagen.parse_reviews import _SYSTEM as LLM_SYSTEM
from core.domain.models import PeerReview, ReviewSection
from core.ingest.convert import to_dataset
from core.ingest.loader import load_bundle
from core.ingest.review_text import NEGATIVE, POSITIVE
from experiments.jev.client import INPUT_USD_PER_1M as JEV_INPUT_USD_PER_1M
from experiments.jev.client import URL as JEV_URL

RESULTS = Path(__file__).parent / "results"
TAG = os.environ.get("E4_TAG", "").strip()
_SUFFIX = f"_{TAG}" if TAG else ""
REPORT = REPO_ROOT / "outputs" / f"review-judge-comparison{_SUFFIX}.html"
SERVICE_SMOKE = RESULTS / "e4_service_smoke.json"
BUNDLE = REPO_ROOT / "demo" / "org-n100"
JEV_INSTRUCTION = "피어리뷰 좋은점/나쁜점 서술 전체의 감성 강도"
JEV_LEVELS = ["매우 부정적", "다소 부정적", "중립", "다소 긍정적", "매우 긍정적"]
WORKERS = 16
SEED = 7
N_FAITHFUL = 300

PROBES = {
    "명백한 부정": ("특별히 없습니다.",
                "약속한 일정을 반복해서 어겼고 팀원과의 소통을 피해 프로젝트 전체가 지연됐습니다. 다시 함께 일하고 싶지 않습니다."),
    "명백한 긍정": ("모든 면에서 탁월했고 팀 성과를 이끌었습니다. 꼭 다시 함께 일하고 싶습니다.", "특별히 없습니다."),
    "시연 원문(항목 1:5)": (
        "금융사 하 데이터 마트 구축 수행 기간 동안 팀의 핵심 인력으로 활약했습니다. 새 도메인 지식을 빠르게 흡수해 투입 초기부터 제 몫을 해냈습니다.",
        "주니어에게 피드백을 줄 때 이유 설명이 짧아 학습 효과가 제한적이었습니다. 또한 리더십, 협업, 전문성, 꼼꼼함 측면도 보완하면 더 좋겠습니다."),
    "같은 항목을 문장으로(1:5)": (
        "새 도메인 지식을 빠르게 흡수해 투입 초기부터 제 몫을 해냈습니다.",
        "주니어에게 피드백을 줄 때 이유 설명이 짧아 학습 효과가 제한적이었습니다. 팀을 이끌어야 할 때 방향 제시가 부족했습니다. "
        "다른 파트와 일정 조율을 하지 않아 충돌이 잦았습니다. 핵심 기술에 대한 이해가 얕아 설계 검토에서 자주 막혔습니다. "
        "산출물에 오류가 많아 재작업이 반복됐습니다."),
}


# --- 데이터 -------------------------------------------------------------------

def item_polarity(r: PeerReview) -> float:
    np_, nn = len(r.positive.items), len(r.negative.items)
    return (np_ - nn) / (np_ + nn)


def load_sets() -> dict[str, list[PeerReview]]:
    bundle, report = load_bundle(BUNDLE)
    ds, _ = to_dataset(bundle, report)
    rng = random.Random(SEED)
    fill = {"name": "데이터 마트 구축", "domain": "금융", "skill": "Python", "area": "데이터", "role": "핵심 담당자"}
    faithful = []
    for i in rng.sample(range(len(ds.reviews)), N_FAITHFUL):
        rv = ds.reviews[i]
        pos = " ".join(rng.choice(POSITIVE[it]).format(**fill) for it in rv.positive.items)
        neg = " ".join(NEGATIVE[it].format(**fill) for it in rv.negative.items)
        faithful.append(PeerReview(reviewer_id=rv.reviewer_id, reviewee_id=rv.reviewee_id,
                                   positive=ReviewSection(items=rv.positive.items, text=pos),
                                   negative=ReviewSection(items=rv.negative.items, text=neg)))
    probes = [PeerReview(reviewer_id="probe", reviewee_id=name, positive=ReviewSection(items=["x"], text=p),
                         negative=ReviewSection(items=["x"], text=n)) for name, (p, n) in PROBES.items()]
    return {"demo": list(ds.reviews), "faithful": faithful, "probe": probes}


# --- 판정기 ---------------------------------------------------------------------

def _retry(fn, attempts: int = 4):
    for a in range(attempts):
        try:
            return fn()
        except Exception:                           # noqa: BLE001 -- 429·일시 오류. 마지막엔 그대로 올린다
            if a == attempts - 1:
                raise
            time.sleep(2 ** a)


def llm_one(client, model: str, r: PeerReview) -> dict:
    def call():
        t = time.monotonic()
        resp = client.chat.completions.create(
            model=model, response_format={"type": "json_object"},
            messages=[{"role": "system", "content": LLM_SYSTEM},
                      {"role": "user", "content": json.dumps({"좋은점": r.positive.text, "나쁜점": r.negative.text},
                                                             ensure_ascii=False)}])
        lat = time.monotonic() - t
        pol = max(-1.0, min(1.0, float(json.loads(resp.choices[0].message.content)["text_polarity"])))
        u = resp.usage
        return {"pol": pol, "lat": lat, "in": u.prompt_tokens, "out": u.completion_tokens}
    return _retry(call)


def jev_one(http: httpx.Client, key: str, r: PeerReview) -> dict:
    body = {"model": "jev-latest", "state": f"피어리뷰 -- 좋은점: {r.positive.text}\n나쁜점: {r.negative.text}",
            "questions": {"polarity": {"type": "score", "instructions": JEV_INSTRUCTION, "criteria": JEV_LEVELS}}}

    def call():
        t = time.monotonic()
        res = http.post(JEV_URL, json=body, headers={"Authorization": f"Bearer {key}"})
        lat = time.monotonic() - t
        res.raise_for_status()
        data = res.json()
        a = data["answers"]["polarity"]
        probs = a.get("probabilities") or {}
        level = (sum(i * float(probs[str(i)]) for i in range(5))
                 if set(probs) == {str(i) for i in range(5)} else float(a["score"]))
        return {"pol": level / 4 * 2 - 1, "lat": lat, "in": int(data.get("usage", {}).get("input_tokens", 0)),
                "out": 0, "model": data.get("model")}
    return _retry(call)


def run(judge: str, name: str, reviews: list[PeerReview]) -> dict:
    path = RESULTS / f"e4_{judge}_{name}{_SUFFIX if judge == 'llm' else ''}.json"
    if path.exists():
        return json.loads(path.read_text("utf-8"))
    load_env()
    if judge == "llm":
        from openai import OpenAI
        from api import review_judge as rj
        url = rj.base_url()
        client, model = OpenAI(base_url=url, api_key=rj.api_key(url) or "EMPTY"), rj.model()
        fn = lambda r: llm_one(client, model, r)
    else:
        http = httpx.Client(timeout=30)
        fn = lambda r: jev_one(http, os.environ["TYPESAFE_API_KEY"], r)
        model = "jev-latest"
    t0 = time.monotonic()
    with ThreadPoolExecutor(WORKERS) as pool:
        rows = list(pool.map(fn, reviews))
    wall = time.monotonic() - t0
    rec = {"judge": judge, "set": name, "model": rows[0].get("model") or model, "workers": WORKERS,
           "wall_s": wall, "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
           "pol": [x["pol"] for x in rows], "lat": [x["lat"] for x in rows],
           "in_tokens": sum(x["in"] for x in rows), "out_tokens": sum(x["out"] for x in rows)}
    RESULTS.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rec, ensure_ascii=False), "utf-8")
    return rec


def cost_usd(rec: dict) -> float:
    if rec["judge"] == "jev":
        return rec["in_tokens"] * JEV_INPUT_USD_PER_1M / 1e6
    price = load_pricing()["models"][load_pricing()["parse_model"]]
    return rec["in_tokens"] * price["input_per_1m"] / 1e6 + rec["out_tokens"] * price["output_per_1m"] / 1e6


# --- 지표 -------------------------------------------------------------------------

def _sign(x):
    return np.where(x > 0.1, 1, np.where(x < -0.1, -1, 0))


def metrics(pred: list[float], truth: list[float] | None) -> dict:
    p = np.asarray(pred)
    d = {"mean": float(p.mean()), "min": float(p.min()), "max": float(p.max()),
         "neg_share": float((p < -0.1).mean()), "pos_share": float((p > 0.1).mean())}
    if truth is not None:
        t = np.asarray(truth)
        rk = lambda x: np.argsort(np.argsort(x, kind="stable"), kind="stable")
        d.update(pearson=float(np.corrcoef(p, t)[0, 1]), spearman=float(np.corrcoef(rk(p), rk(t))[0, 1]),
                 mae=float(np.abs(p - t).mean()), sign_agree=float((_sign(p) == _sign(t)).mean()),
                 neg_recall=float((p[t < 0] < -0.1).mean()))
    return d


def bands(pred: list[float], truth: list[float]) -> list[dict]:
    p, t = np.asarray(pred), np.asarray(truth)
    out = []
    for label, lo, hi in (("많이 부정 (−1 ~ −0.4)", -1.01, -0.4), ("조금 부정 (−0.4 ~ 0)", -0.4, -0.01),
                          ("중립 (0)", -0.01, 0.01), ("조금 긍정 (0 ~ +0.4)", 0.01, 0.4), ("많이 긍정 (+0.4 ~ +1)", 0.4, 1.0)):
        m = (t > lo) & (t <= hi)
        out.append({"band": label, "n": int(m.sum()), "mean": float(p[m].mean()) if m.any() else None})
    return out


def summarize() -> dict:
    sets = load_sets()
    out = {"sets": {}, "probe": {}}
    for name in ("demo", "faithful"):
        truth = [item_polarity(r) for r in sets[name]]
        recs = {j: run(j, name, sets[name]) for j in ("llm", "jev")}
        out["sets"][name] = {
            "n": len(truth), "items": metrics(truth, None),
            "judges": {j: {**metrics(r["pol"], truth), "model": r["model"], "wall_s": r["wall_s"],
                           "lat_median_s": float(np.median(r["lat"])), "lat_p95_s": float(np.percentile(r["lat"], 95)),
                           "in_tokens": r["in_tokens"], "out_tokens": r["out_tokens"], "cost_usd": cost_usd(r),
                           "per_1000_usd": cost_usd(r) / len(truth) * 1000,
                           "workers": r["workers"], "recorded_at": r["recorded_at"]} for j, r in recs.items()},
            "llm_vs_jev_pearson": float(np.corrcoef(recs["llm"]["pol"], recs["jev"]["pol"])[0, 1]),
            "bands": {j: bands(r["pol"], truth) for j, r in recs.items()},
        }
    probe = {j: run(j, "probe", sets["probe"]) for j in ("llm", "jev")}
    out["probe"] = {name: {j: probe[j]["pol"][i] for j in probe} for i, name in enumerate(PROBES)}
    out["service_smoke"] = json.loads(SERVICE_SMOKE.read_text("utf-8")) if SERVICE_SMOKE.exists() else None
    (RESULTS / f"e4_summary{_SUFFIX}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), "utf-8")
    return out


# --- 보고서 -------------------------------------------------------------------------

def _f(x, nd=2, pct=False):
    if x is None:
        return "–"
    return f"{x * 100:.0f}%" if pct else f"{x:+.{nd}f}" if nd and x < 0 or nd and x > 0 else f"{x:.{nd}f}"


def _smoke(sm: dict | None) -> str:
    if not sm:
        return ""
    rows = "".join(f"<li>{html.escape(r)}</li>" for r in sm["observations"])
    return (f'<div class="box"><b>서비스 경로 실측({html.escape(sm["recorded_at"])}, <code>api/review_judge.py</code>, '
            f'{html.escape(sm["data"])}) — {html.escape(sm["method"])}</b><ul>{rows}</ul></div>')


def render(s: dict) -> str:
    e = html.escape
    d, f = s["sets"]["demo"], s["sets"]["faithful"]
    L, J = "LLM (gpt-6-luna)", "Jev"

    def row(label, key, data, pct=False, nd=2):
        return (f"<tr><td>{e(label)}</td><td>{_f(data['judges']['llm'][key], nd, pct)}</td>"
                f"<td>{_f(data['judges']['jev'][key], nd, pct)}</td></tr>")

    def cost_rows(data):
        rows = ""
        for j, name in (("llm", L), ("jev", J)):
            x = data["judges"][j]
            rows += (f"<tr><td>{name}</td><td>{x['wall_s']:.0f}초 (병렬 {x['workers']})</td><td>{x['lat_median_s']:.2f}초 / "
                     f"{x['lat_p95_s']:.2f}초</td><td>{x['in_tokens']:,} / {x['out_tokens']:,}</td>"
                     f"<td>${x['cost_usd']:.4f}</td><td>${x['per_1000_usd']:.4f}</td></tr>")
        return rows

    band_rows = "".join(
        f"<tr><td>{e(b['band'])}</td><td>{b['n']}</td><td>{_f(b['mean'])}</td><td>{_f(f['bands']['jev'][i]['mean'])}</td></tr>"
        for i, b in enumerate(f["bands"]["llm"]))
    probe_rows = "".join(f"<tr><td>{e(k)}</td><td>{_f(v['llm'])}</td><td>{_f(v['jev'])}</td></tr>"
                         for k, v in s["probe"].items())
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>리뷰 판정기 비교</title>
<style>
body{{font-family:-apple-system,"Apple SD Gothic Neo","Noto Sans KR",sans-serif;background:#fafaf7;color:#1f2328;margin:0;line-height:1.7}}
main{{max-width:860px;margin:0 auto;padding:32px 16px 64px}} h1{{font-size:1.6rem}} h2{{font-size:1.2rem;margin-top:2rem;border-left:4px solid #2563eb;padding-left:.6rem}}
table{{border-collapse:collapse;width:100%;font-size:.93rem;margin:10px 0;background:#fff}} td,th{{border:1px solid #e5e7eb;padding:6px 8px;text-align:left}}
th{{background:#f3f4f6}} .box{{background:#fff;border:1px solid #e5e7eb;border-radius:10px;padding:12px 16px;margin:12px 0}}
.warn{{background:#fffbeb;border-color:#fcd34d}} .ok{{background:#ecfdf5;border-color:#6ee7b7}} .muted{{color:#6b7280;font-size:.88rem}}
</style></head><body><main>
<h1>리뷰 글 판정기 비교: LLM vs Jev</h1>
<p class="muted">실험 E4 · 기록 {e(d['judges']['llm']['recorded_at'][:10])} · 코드 <code>experiments/jev/e4_judges.py</code> ·
원시 결과 <code>experiments/jev/results/e4_*.json</code> · 모든 데이터는 가상(합성)이며 사업 효과는 NOT_CALIBRATED</p>

<div class="box ok"><b>결정(사용자, 2026-10-06)</b>: 리뷰 글 판정은 선택지 없이 <b>LLM(OpenAI 호환 API)</b>으로 통일한다.
사내 온프렘 LLM은 주소(<code>TEAMWEAVER_REVIEW_BASE_URL</code>)만 바꿔 같은 방식으로 쓴다. 규칙 기반·Jev 선택지는 없앤다.</div>

<h2>1. 왜 비교했나</h2>
<p>협업 점수의 리뷰 부분은 <code>쌍 리뷰 점수 = 0.5 × 항목 점수 + 0.5 × 글 점수</code>다. 지금까지 기본이던 규칙 기반은
글 점수 자리에 항목 균형을 다시 넣어, 평가 사유(글)를 점수에 전혀 쓰지 않았다. 항목을 두 번 쓴 셈이다.
글을 실제로 읽는 판정기 두 후보(LLM, 판단 전용 모델 Jev)를 같은 글로 비교했다.</p>

<h2>2. 방법</h2>
<ul>
<li><b>A. 시연 원문</b> — 시연 묶음 100명의 리뷰 {d['n']:,}건. 생성기가 아쉬운 점을 한 줄로 몰아 쓰고 부드럽게 써서,
글이 항목만큼 부정적이지 않다. 정답이 없으므로 분포만 본다.</li>
<li><b>B. 충실한 글</b> — 같은 리뷰에서 {f['n']}건을 뽑아, 고른 항목을 빠짐없이 한 문장씩 다시 쓴 글. 글이 항목을 그대로 담으므로
<b>정답 = 항목 균형</b>((좋은 점 수 − 아쉬운 점 수) ÷ 전체 항목 수).</li>
<li><b>대조 문장</b> — 명백한 부정·긍정 등 4건으로 척도(−1~+1)가 살아 있는지 확인.</li>
<li>LLM 지시문은 서비스 파서와 같다("서술 전체의 감성 강도", −1~1 JSON). Jev는 5단계 점수(0=매우 부정 … 4=매우 긍정)의
확률 기댓값을 −1~1로 옮겼다. 부호 판정의 중립 구간은 ±0.1.</li>
</ul>

<h2>3. 결과</h2>
<h3>B. 충실한 글 {f['n']}건 (정답 있음)</h3>
<table><tr><th>지표</th><th>{L}</th><th>{J}</th></tr>
{row("정답과 상관(피어슨)", "pearson", f)}{row("정답과 순위 상관(스피어만)", "spearman", f)}
{row("정답과 평균 절대 차이(작을수록 좋음)", "mae", f)}{row("긍정·중립·부정 일치율", "sign_agree", f, pct=True)}
{row("실제 부정 리뷰를 부정으로 잡은 비율", "neg_recall", f, pct=True)}{row("평균 판정값 (정답 평균 " + _f(f['items']['mean']) + ")", "mean", f)}
</table>
<table><tr><th>정답(항목) 구간</th><th>건수</th><th>{L} 평균</th><th>{J} 평균</th></tr>{band_rows}</table>

<h3>A. 시연 원문 {d['n']:,}건 (정답 없음)</h3>
<table><tr><th>지표</th><th>{L}</th><th>{J}</th></tr>
{row("평균 판정값 (항목 균형 평균 " + _f(d['items']['mean']) + ")", "mean", d)}
{row("부정(−0.1 미만)으로 판정한 비율 (항목 기준 " + _f(d['items']['neg_share'], pct=True) + ")", "neg_share", d, pct=True)}
{row("항목 균형과 상관", "pearson", d)}
<tr><td>두 판정기끼리 상관</td><td colspan="2">{d['llm_vs_jev_pearson']:.2f}</td></tr></table>

<h3>대조 문장</h3>
<table><tr><th>글</th><th>{L}</th><th>{J}</th></tr>{probe_rows}</table>

<h2>4. 비용과 소요 시간</h2>
<table><tr><th>판정기</th><th>총 소요 시간</th><th>건당 지연 중앙값 / 95%</th><th>입력 / 출력 토큰</th><th>비용</th><th>1,000건당</th></tr>
<tr><th colspan="6">A. 시연 원문 {d['n']:,}건</th></tr>{cost_rows(d)}
<tr><th colspan="6">B. 충실한 글 {f['n']}건</th></tr>{cost_rows(f)}</table>
<p class="muted">비용 = 토큰 × 공식 단가(gpt-6-luna 입력 $0.10·출력 $0.50 / 100만 토큰, Jev 입력 $0.042 / 100만 토큰·출력 무료,
2026-10 기준). 시간은 병렬 {WORKERS}건 동시 호출 기준이며 서비스는 한 번 판정한 글을 저장해 두 번째부터는 즉시다.
사내 온프렘 LLM은 토큰 비용 대신 사내 GPU 처리량이 시간을 정한다.</p>
{_smoke(s.get("service_smoke"))}

<h2>5. 해석</h2>
<ul>
<li><b>순위는 둘 다 잘 맞힌다</b>(B에서 상관 0.94 안팎). 차이는 <b>부정 검출</b>이다. LLM은 실제 부정 리뷰의
{_f(f['judges']['llm']['neg_recall'], pct=True)}를 부정으로 읽었고, Jev는 {_f(f['judges']['jev']['neg_recall'], pct=True)}였다.
Jev는 "조금 아쉬웠습니다"처럼 부드럽게 쓴 지적을 긍정으로 읽는다(명백한 부정 문장은 −1로 잘 잡는다).
아쉬운 점을 부드럽게 쓰는 조직일수록 이 차이가 크다.</li>
<li><b>A에서는 두 판정기 모두 긍정으로 쏠렸다.</b> 판정기 문제가 아니라 시연 생성기의 글이 항목보다 부드럽기 때문이다
(아쉬운 점을 한 줄로 몰아 쓰고, 모든 리뷰가 칭찬 문장으로 시작). 생성기 수정은 데이터 담당(claude-a)에 요청했다.</li>
<li><b>Jev는 약 {d['judges']['llm']['wall_s'] / max(d['judges']['jev']['wall_s'], 1e-9):.0f}배 빠르고 더 싸다.</b> 하지만 판정은 데이터를 올릴 때 한 번이고
결과를 저장하므로, 시간보다 부정 검출이 중요하다고 판단했다.</li>
<li>두 판정기 모두 중립 리뷰를 약간 긍정으로 읽는 경향이 있다(B 중립 구간 평균이 +0.3 안팎).</li>
</ul>

<h2>6. 한계</h2>
<div class="box warn"><ul>
<li><b>결정 근거(부정 검출 92% 대 17%)는 B(생성기 문장으로 다시 쓴 글)의 결과다.</b> 서비스가 지금 실제로 판정할
시연 원문(A)에서는 두 판정기 모두 부정을 거의 못 잡았다(부정 검출 LLM {_f(d['judges']['llm']['neg_recall'], pct=True)}·
Jev {_f(d['judges']['jev']['neg_recall'], pct=True)}, 부호 일치 {_f(d['judges']['llm']['sign_agree'], pct=True)}·
{_f(d['judges']['jev']['sign_agree'], pct=True)}). 그래서 이번 전환은 지금 시연 데이터의 리뷰 점수를 크게 긍정 쪽으로 옮긴다
(평균 판정값 {_f(d['judges']['llm']['mean'])} 대 항목 균형 {_f(d['items']['mean'])}). 이것이 배치 결과에 주는 영향은
재지 않았다 — 시연 생성기 글이 고쳐지면(claude-a 요청) A를 다시 재야 한다.</li>
<li>정답은 "항목 균형"이다. 이것은 글을 만든 의도이지 실제 협업 성과가 아니다(NOT_CALIBRATED).</li>
<li>B의 글은 생성기의 문장 은행으로 만들었다. 실제 사람의 글(직설적인 평가 포함)과는 표현 폭이 다르다.</li>
<li>LLM 결과는 OpenAI gpt-6-luna 기준이다. 사내 온프렘 모델은 같은 시험으로 다시 재야 한다 —
<code>E4_TAG=onprem TEAMWEAVER_REVIEW_BASE_URL=… TEAMWEAVER_REVIEW_MODEL=…</code>로 돌리면 기존 기록을 재사용하지 않고 새로 잰다.</li>
<li>이전 실험 E2(가상 fixture 266건, <code>outputs/jev-experiment.html</code>)에서도 같은 방향(LLM 부호 일치 74% 대 Jev 61%)이었다.</li>
</ul></div>
</main></body></html>"""


def main() -> None:
    s = summarize()
    REPORT.write_text(render(s), "utf-8")
    print(json.dumps({k: {j: {m: round(v, 3) if isinstance(v, float) else v for m, v in x.items()}
                          for j, x in d["judges"].items()} for k, d in s["sets"].items()}, ensure_ascii=False, indent=1))
    print("probe", json.dumps(s["probe"], ensure_ascii=False))
    print("report:", REPORT)


if __name__ == "__main__":
    main()
