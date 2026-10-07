"""E4. 리뷰 글 판정기 비교: 외부 LLM(gpt-6-luna) vs 사내 LLM(GLM 5.3) vs Jev -- 정확도·비용·소요 시간.

2026-10-07 사용자 지시: 비교는 전부 추론 low, 유의미한 표본 크기만. 그래서 주 비교는 외부 low(llm_low) 대 사내 low(glm_low)이고,
외부 기본 추론(llm, 서비스 현재 설정)·사내 max(glm, 2026-10-06 기록)는 참고 열이다. 시험은 대조 문장 4·충실한 글 300(정답 있음)·
현재 시연 원문 무작위 300(demo_s300). 예전 전체 시연 원문 기록(e4_*_demo*.json)은 데이터가 바뀌기 전 것이라 쓰지 않는다(이력).

사용자 지시(2026-10-06): LLM을 쓰는 모든 곳은 외부 AI(gpt-6-luna)와 회사가 제공하는 오픈소스 LLM(GLM 5.3)을 항상
함께 재서 결과를 각각 낸다. 사내 서버로는 측정할 수 없어 Z.ai 공식 API의 GLM 5.3을 사내 LLM으로 가정한다(사용자 결정 2026-10-07) -- Z.ai 공식 API(`https://api.z.ai/api/paas/v4`, 모델
`glm-5.3`)로 재며 가상 데이터만 보낸다. 주 비교는 추론 low, max는 참고 열이다. 실제 온프렘이면 서빙 환경(양자화·하드웨어)이
달라 결과·속도가 조금 다를 수 있다. 비용은 Z.ai 요금 기준이다.

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
- glm: 같은 지시문·같은 JSON 형식으로 GLM 5.3(추론 max)에 묻는다. 키는 `ZAI_API_KEY`.
- jev: Score 5단계(0=매우 부정 … 4=매우 긍정) → 등급 확률 기댓값 → [-1, 1] (E2와 같은 지시문).

기록: 판정값·건당 지연·토큰 사용량을 `experiments/jev/results/e4_<판정기>_<시험>.json`에 남기고, 있으면 다시
부르지 않는다. 비용 = 토큰 × 공식 단가(core/config pricing.json의 gpt-6-luna, Jev 입력 $0.042/100만·출력 무료).
보고서: `outputs/review-judge-comparison.html`.

실행: `uv run --group benchmark python -m experiments.jev.e4_judges` (OPENAI_API_KEY·TYPESAFE_API_KEY·ZAI_API_KEY 필요,
기록이 있으면 키 없이 보고서만 다시 만든다 -- 중간 기록(.partial.json)도 그대로 부분 결과로 쓴다. 이어서 판정하려면
E4_RESUME=1). 기록마다 판정한 글의 지문(fingerprint)을 남겨, 데이터 생성기 문구가 바뀌면 섞지 않고 멈춘다.
다른 OpenAI 호환 엔드포인트로 재기(예: 나중에 사내 서버가 열리면 -- 2026-10-07 현재는 사내 측정 불가, Z.ai를 사내로 가정):
`E4_TAG=onprem TEAMWEAVER_REVIEW_BASE_URL=<주소> TEAMWEAVER_REVIEW_MODEL=<모델>
TEAMWEAVER_REVIEW_API_KEY=<있으면> uv run ... -m experiments.jev.e4_judges` -- 결과 파일·보고서 이름에 태그가 붙어
기존 OpenAI 기록을 재사용하지 않는다(서비스 판정기와 같은 주소·모델·키 규칙, api/review_judge.py)."""
from __future__ import annotations

import hashlib
import html
import json
import os
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
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
JUDGES = ("llm_low", "glm_low", "llm", "glm", "jev")
# 시험마다 돌리는 판정기. GLM max는 비용 때문에(1,000건당 약 $4) 같은 글이 남은 시험에서만 참고 열로 쓴다
# (2026-10-07 사용자 지시: 비교는 전부 low, 유의미한 표본 크기만).
# 사용자 지시 "전부 low": 주 비교는 외부 low 대 사내 low. 외부 기본(서비스 현재 설정)과 사내 max는 참고 열.
SET_JUDGES = {"probe": JUDGES, "faithful": JUDGES,
              "demo_s300": ("llm_low", "glm_low", "llm", "jev")}
N_DEMO_SAMPLE = 300           # 일치율의 95% 구간 반폭 ≤ ±5.7%p(1.96·√(0.25/300))
# 사내 열 = Z.ai 공식 API의 GLM 5.3을 사내 LLM으로 가정(사용자 결정 2026-10-07: 사내 서버 측정 불가). 표만 인용돼도
# 무엇을 쟀는지 남게 이름에 Z.ai를 적는다.
LABEL = {"llm_low": "외부 LLM low (gpt-6-luna)", "glm_low": "사내 LLM low (GLM 5.3·Z.ai)",
         "llm": "외부 LLM 기본 추론·참고 (gpt-6-luna, 서비스 현재 설정)",
         "glm": "사내 LLM max·참고 (GLM 5.3·Z.ai)", "jev": "Jev"}
GLM_URL = "https://api.z.ai/api/paas/v4"
GLM_MODEL = "glm-5.3"
GLM_REASONING = "max"                # 사용자 지정(2026-10-06). 공식 API는 추론을 끌 수 없다.
# Z.ai 공식 단가(2026-10, docs.z.ai/guides/overview/pricing): 입력 $1.40, 캐시된 입력 $0.26, 출력 $4.40 / 100만 토큰.
# 추론 토큰은 completion_tokens에 포함돼 출력으로 센다.
GLM_PRICE = {"input": 1.40, "cached": 0.26, "output": 4.40}
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
    # 현재 시연 원문에서 무작위 300건(충실한 글과 다른 난수열 -- 기존 충실한 글 300건이 그대로 같게)
    demo_idx = sorted(random.Random(SEED + 100).sample(range(len(ds.reviews)), N_DEMO_SAMPLE))
    return {"demo": list(ds.reviews), "demo_s300": [ds.reviews[i] for i in demo_idx], "faithful": faithful,
            "probe": probes}


# --- 판정기 ---------------------------------------------------------------------

class FatalApiError(RuntimeError):
    """다시 보내도 소용없는 오류(잔액 부족·키 거절). 바로 멈춘다 -- 받은 결과는 중간 기록에 남아 있다."""


def _is_fatal(exc: Exception) -> bool:
    """구조로 판별한다(문자열에 '1113'이 우연히 섞인 일시 오류를 치명으로 오인하지 않게, 리뷰 S6).
    openai SDK: APIStatusError.status_code·body["error"]["code"]. httpx: exc.response.status_code."""
    status = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
    body = getattr(exc, "body", None)
    err = body.get("error", body) if isinstance(body, dict) else None
    code = str(err.get("code")) if isinstance(err, dict) and err.get("code") is not None else None
    return code == "1113" or status in (401, 403)          # 1113 = Z.ai 잔액 부족


def _retry(fn, attempts: int = 4):
    """일시 오류는 지수 대기 후 다시(최대 8초 간격). 잔액 부족·키 거절은 다시 보내지 않는다."""
    for a in range(attempts):
        try:
            return fn()
        except Exception as exc:                    # noqa: BLE001 -- 429·일시 오류. 마지막엔 그대로 올린다
            if _is_fatal(exc):
                raise FatalApiError(str(exc)[:200]) from None
            if a == attempts - 1:
                raise
            time.sleep(min(2 ** a, 8))


def llm_one(client, model: str, r: PeerReview, extra: dict | None = None) -> dict:
    def call():
        t = time.monotonic()
        resp = client.chat.completions.create(
            model=model, response_format={"type": "json_object"}, extra_body=extra or None,
            messages=[{"role": "system", "content": LLM_SYSTEM},
                      {"role": "user", "content": json.dumps({"좋은점": r.positive.text, "나쁜점": r.negative.text},
                                                             ensure_ascii=False)}])
        lat = time.monotonic() - t
        pol = max(-1.0, min(1.0, float(json.loads(resp.choices[0].message.content)["text_polarity"])))
        u = resp.usage
        ptd = getattr(u, "prompt_tokens_details", None)
        ctd = getattr(u, "completion_tokens_details", None)
        return {"pol": pol, "lat": lat, "in": u.prompt_tokens, "out": u.completion_tokens,
                "cached": int(getattr(ptd, "cached_tokens", 0) or 0) if ptd else 0,
                "reasoning": int(getattr(ctd, "reasoning_tokens", 0) or 0) if ctd else 0,
                "model": getattr(resp, "model", None)}
    return _retry(call, attempts=6)


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


def fingerprint(reviews: list[PeerReview]) -> str:
    """판정한 글의 지문. 데이터 생성기 문구가 바뀌면(병합 등) 예전 기록을 새 글의 결과로 잘못 쓰지 않게 대조한다(리뷰 S7)."""
    h = hashlib.sha256()
    for r in reviews:
        h.update(json.dumps([r.positive.text, r.negative.text], ensure_ascii=False).encode("utf-8"))
    return h.hexdigest()


def _settings(judge: str) -> dict:
    return {"glm": {"reasoning_effort": GLM_REASONING}, "glm_low": {"reasoning_effort": "low"},
            "llm_low": {"reasoning_effort": "low"}}.get(judge, {})


def _atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), "utf-8")
    os.replace(tmp, path)              # 쓰는 도중 죽어도 중간 기록이 깨지지 않게


def _record(judge, name, model, rows, idx, wall, fp, *, partial=False, stop_reason=None) -> dict:
    rec = {"judge": judge, "set": name, "model": (rows[0].get("model") if rows else None) or model,
           "workers": WORKERS, "wall_s": wall, "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
           "pol": [x["pol"] for x in rows], "lat": [x["lat"] for x in rows],
           "in_tokens": sum(x["in"] for x in rows), "out_tokens": sum(x["out"] for x in rows),
           "cached_tokens": sum(x.get("cached", 0) for x in rows),
           "reasoning_tokens": sum(x.get("reasoning", 0) for x in rows),
           "settings": _settings(judge), "fingerprint": fp}
    if partial:
        rec.update(indices=idx, partial=True, stop_reason=stop_reason)
    return rec


def _check_fp(found: str | None, fp: str, path: Path) -> None:
    if found is not None and found != fp:
        raise ValueError(f"{path.name}은 지금과 다른 리뷰 글로 판정한 기록이다(데이터 생성기 변경?). 섞지 않도록 멈춘다 -- "
                         "기록을 옮기고 다시 재거나 E4_TAG로 다른 이름을 쓴다.")


def run(judge: str, name: str, reviews: list[PeerReview], resume: bool | None = None) -> dict:
    """판정 기록을 읽거나(있으면 API를 부르지 않는다) 새로 잰다.

    중간 기록(.partial.json)이 있으면 기본은 **그대로 부분 결과로 쓴다** -- 보고서를 다시 만들 때 남은 건을 몰래 API로
    부르지 않게(리뷰 S1, 비용). 이어서 판정하려면 E4_RESUME=1."""
    resume = os.environ.get("E4_RESUME") == "1" if resume is None else resume
    path = RESULTS / f"e4_{judge}_{name}{_SUFFIX if judge in ('llm', 'llm_low') else ''}.json"
    fp = fingerprint(reviews)
    if path.exists():
        rec = json.loads(path.read_text("utf-8"))
        _check_fp(rec.get("fingerprint"), fp, path)
        if rec.get("fingerprint") is None:            # 지문 이전 기록: 지금 글과 같다고 보고 지문을 남긴다(같은 커밋에서 잰 기록)
            rec["fingerprint"] = fp
            _atomic_json(path, rec)
        return rec
    partial_path = path.with_suffix(".partial.json")
    partial = (json.loads(partial_path.read_text("utf-8")) if partial_path.exists()
               else {"rows": {}, "wall_s": 0.0, "fingerprint": fp, "settings": _settings(judge)})
    _check_fp(partial.get("fingerprint"), fp, partial_path)
    if partial_path.exists() and ("fingerprint" not in partial or "settings" not in partial):
        # 지문 이전 중간 기록: 지금 글과 같다고 보고 지문을 남긴다 -- 이후 데이터가 바뀌면 섞이지 않고 멈추게(리뷰 2라운드)
        partial.setdefault("fingerprint", fp)
        partial.setdefault("settings", _settings(judge))
        _atomic_json(partial_path, partial)
    partial.setdefault("fingerprint", fp)
    partial.setdefault("settings", _settings(judge))
    done_rows: dict = partial["rows"]

    def partial_record(reason: str | None) -> dict:
        idx = sorted(int(i) for i in done_rows)
        return _record(judge, name, None, [done_rows[str(i)] for i in idx], idx, partial["wall_s"], fp,
                       partial=True, stop_reason=reason)

    if partial_path.exists() and not resume:
        return partial_record(partial.get("stop_reason") or "중단 사유 기록 없음")
    load_env()
    if judge in ("llm", "llm_low"):
        from openai import OpenAI
        from api import review_judge as rj
        url = rj.base_url()
        client, model = OpenAI(base_url=url, api_key=rj.api_key(url) or "EMPTY", max_retries=0), rj.model()
        extra = {"reasoning_effort": "low"} if judge == "llm_low" else None
        fn = lambda r: llm_one(client, model, r, extra)
    elif judge in ("glm", "glm_low"):
        from openai import OpenAI
        client, model = OpenAI(base_url=GLM_URL, api_key=os.environ["ZAI_API_KEY"], timeout=300, max_retries=0), GLM_MODEL
        effort = _settings(judge)["reasoning_effort"]
        fn = lambda r: llm_one(client, model, r, {"reasoning_effort": effort})
    else:
        http = httpx.Client(timeout=30)
        fn = lambda r: jev_one(http, os.environ["TYPESAFE_API_KEY"], r)
        model = "jev-latest"
    # 중간 기록: 한 건 받을 때마다 남긴다(2026-10-06 GLM 실측: 끝에서만 저장해 중간에 멈추자 받은 결과를 모두 잃었다).
    lock = threading.Lock()
    stop = threading.Event()
    t0 = time.monotonic()

    def one(ix_r):
        ix, r = ix_r
        if stop.is_set():                    # 치명 오류 뒤에는 더 보내지 않는다(리뷰 S2)
            return None
        try:
            row = fn(r)
        except FatalApiError:
            stop.set()
            raise
        with lock:
            done_rows[str(ix)] = row
            _atomic_json(partial_path, partial)
        return row

    todo = [(i, r) for i, r in enumerate(reviews) if str(i) not in done_rows]
    stop_reason = None
    pool = ThreadPoolExecutor(WORKERS)
    try:
        futures = [pool.submit(one, t) for t in todo]
        for f in as_completed(futures):
            f.result()
    except FatalApiError as exc:
        stop_reason = str(exc)[:200]         # 받은 만큼으로 부분 결과(정확도는 같은 리뷰끼리만 비교)
    finally:
        stop.set()
        pool.shutdown(wait=True, cancel_futures=True)
        partial["wall_s"] += time.monotonic() - t0
        partial["stop_reason"] = stop_reason
        _atomic_json(partial_path, partial)
    if stop_reason is not None:
        return partial_record(stop_reason)
    rec = _record(judge, name, model, [done_rows[str(i)] for i in range(len(reviews))], None, partial["wall_s"], fp)
    _atomic_json(path, rec)
    partial_path.unlink(missing_ok=True)
    return rec


def cost_usd(rec: dict) -> float:
    if rec["judge"] == "jev":
        return rec["in_tokens"] * JEV_INPUT_USD_PER_1M / 1e6
    if rec["judge"] in ("glm", "glm_low"):
        cached = rec.get("cached_tokens", 0)
        return ((rec["in_tokens"] - cached) * GLM_PRICE["input"] + cached * GLM_PRICE["cached"]
                + rec["out_tokens"] * GLM_PRICE["output"]) / 1e6
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


def _wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    if n == 0:
        return None
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)
    return ((c - h) / d, (c + h) / d)


def _mcnemar_exact(b: int, c: int) -> float:
    """짝지은 두 판정기의 맞힘 차이(한쪽만 맞힌 건 b·c)에 대한 양측 정확 검정 p값."""
    n = b + c
    if n == 0:
        return 1.0
    from math import comb
    tail = sum(comb(n, i) for i in range(0, min(b, c) + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def _paired_stats(recs: dict, truth: list[float], first: str = "llm", second: str = "glm_low") -> dict:
    """두 판정기 차이가 이 표본에서 확정되는가(리뷰 S3): 부정 검출·부호 일치의 McNemar 정확검정, Wilson 95% 구간."""
    t = np.asarray(truth)
    if first not in recs or second not in recs or len(t) == 0:
        return {}
    a, g = np.asarray(recs[first]["pol"]), np.asarray(recs[second]["pol"])
    neg = t < 0
    ha, hg = (a[neg] < -0.1), (g[neg] < -0.1)
    sa, sg = (_sign(a) == _sign(t)), (_sign(g) == _sign(t))
    return {
        "pair": [first, second],
        "neg_n": int(neg.sum()),
        "neg_ci": {first: _wilson(int(ha.sum()), int(neg.sum())), second: _wilson(int(hg.sum()), int(neg.sum()))},
        "neg_only": {first: int((ha & ~hg).sum()), second: int((hg & ~ha).sum())},
        "neg_p": _mcnemar_exact(int((ha & ~hg).sum()), int((hg & ~ha).sum())),
        "sign_only": {first: int((sa & ~sg).sum()), second: int((sg & ~sa).sum())},
        "sign_p": _mcnemar_exact(int((sa & ~sg).sum()), int((sg & ~sa).sum())),
    }


def summarize() -> dict:
    sets = load_sets()
    out = {"sets": {}, "probe": {}}
    # 정답이 있는 시험(대조 문장·충실한 글)부터 돈다 -- 잔액이 모자라면 시연 원문이 부분 결과가 된다.
    probe = {j: run(j, "probe", sets["probe"]) for j in SET_JUDGES["probe"]}
    for name in ("faithful", "demo_s300"):
        judges = SET_JUDGES[name]
        full = [item_polarity(r) for r in sets[name]]
        raw = {j: run(j, name, sets[name]) for j in judges}
        # 부분 결과가 있으면 모든 판정기가 판정한 같은 리뷰끼리만 비교한다(공정 비교).
        common = sorted(set.intersection(*(set(r.get("indices", range(len(full)))) for r in raw.values())))
        recs = {}
        for j, r in raw.items():
            pos = {ix: k for k, ix in enumerate(r.get("indices", range(len(full))))}
            # 정확도는 같은 리뷰(common)끼리, 시간·비용은 그 판정기가 실제로 판정한 건 전부 기준(리뷰 S4)
            recs[j] = {**r, "pol": [r["pol"][pos[i]] for i in common], "lat_own": list(r["lat"]), "n_judged": len(pos)}
        truth = [full[i] for i in common]
        out["sets"][name] = {
            "n": len(truth), "n_total": len(full),
            "partial": {j: {"judged": r["n_judged"], "stop_reason": r.get("stop_reason")}
                        for j, r in recs.items() if r.get("partial")},
            "items": metrics(truth, None),
            "judges": {j: {**metrics(r["pol"], truth), "model": r["model"], "wall_s": r["wall_s"],
                           "lat_median_s": float(np.median(r["lat_own"])), "lat_p95_s": float(np.percentile(r["lat_own"], 95)),
                           "partial": bool(r.get("partial")),
                           "in_tokens": r["in_tokens"], "out_tokens": r["out_tokens"], "cost_usd": cost_usd(r),
                           "reasoning_tokens": r.get("reasoning_tokens"), "settings": r.get("settings", {}),
                           "per_1000_usd": cost_usd(r) / max(r["n_judged"], 1) * 1000, "n_judged": r["n_judged"],
                           "workers": r["workers"], "recorded_at": r["recorded_at"]} for j, r in recs.items()},
            "pairwise_pearson": {f"{a}~{b}": float(np.corrcoef(recs[a]["pol"], recs[b]["pol"])[0, 1])
                                 for i, a in enumerate(judges) for b in judges[i + 1:]},
            "bands": {j: bands(r["pol"], truth) for j, r in recs.items()},
            "stats": _paired_stats(recs, truth, "llm_low", "glm_low"),
            "stats_low_vs_max": _paired_stats(recs, truth, "glm_low", "glm"),
        }
    out["probe"] = {name: {j: (probe[j]["pol"][probe[j]["indices"].index(i)]
                               if probe[j].get("partial") and i in probe[j]["indices"]
                               else (probe[j]["pol"][i] if not probe[j].get("partial") else None))
                           for j in probe} for i, name in enumerate(PROBES)}
    out["service_smoke"] = json.loads(SERVICE_SMOKE.read_text("utf-8")) if SERVICE_SMOKE.exists() else None
    out["demo_total"] = len(sets["demo"])
    (RESULTS / f"e4_summary{_SUFFIX}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), "utf-8")
    return out


# --- 보고서 -------------------------------------------------------------------------

def _f(x, nd=2, pct=False, sign=True):
    if x is None:
        return "–"
    if pct:
        return f"{x * 100:.0f}%"
    return f"{x:+.{nd}f}" if sign and x != 0 else f"{x:.{nd}f}"


def _p(x: float) -> str:
    return "p&lt;0.001" if x < 0.001 else f"p≈{x:.2f}" if x >= 0.1 else f"p≈{x:.3f}"


def _stats_text(st: dict) -> str:
    """두 판정기(st['pair'])의 짝지은 비교 문장. p < 0.05면 '차이가 있다', 아니면 '확정되지 않는다'."""
    if not st:
        return ""
    a, b = st["pair"]
    ci = st["neg_ci"]
    fmt = lambda c: "–" if c is None else f"{c[0] * 100:.0f}~{c[1] * 100:.0f}%"
    verdict = ("부정 검출 차이가 이 표본에서 통계적으로 확인된다" if st["neg_p"] < 0.05
               else "부정 검출 차이는 이 표본에서 통계적으로 확정되지 않는다")
    verdict += "(부호 일치 차이도 확인)" if st["sign_p"] < 0.05 else "(부호 일치 차이는 확정 안 됨)"
    return (f"<b>{html.escape(LABEL[a])} 대 {html.escape(LABEL[b])}: {verdict}</b> — 실제 부정 리뷰 {st['neg_n']}건 중 한쪽만 맞힌 건 "
            f"{st['neg_only'][a]}건 대 {st['neg_only'][b]}건(McNemar 정확검정 {_p(st['neg_p'])}), 부정 검출 95% 구간 "
            f"{fmt(ci[a])} 대 {fmt(ci[b])}. 부호 일치에서 한쪽만 맞힌 건 {st['sign_only'][a]}건 대 {st['sign_only'][b]}건"
            f"({_p(st['sign_p'])}).")


def _stop_words(d: dict) -> str:
    reasons = [i.get("stop_reason") or "" for i in d.get("partial", {}).values()]
    return "API 잔액 소진으로 중단" if any("1113" in r or "balance" in r for r in reasons) else "판정 중단"


def _partial_note(s: dict) -> str:
    items = []
    for name, d in s["sets"].items():
        for j, info in d.get("partial", {}).items():
            reason = info["stop_reason"] or ""
            why = "API 잔액 소진" if ("1113" in reason or "balance" in reason) else html.escape(reason[:80])
            items.append(f"{'시연 원문' if name.startswith('demo') else '충실한 글'}: {LABEL[j]} {info['judged']:,}/{d['n_total']:,}건만 판정"
                         f"({why}) — 정확도 표는 모든 판정기가 판정한 같은 {d['n']:,}건끼리 비교, 비용·시간 표는 각 판정기가 "
                         f"실제로 판정한 건수 기준")
    if not items:
        return ""
    return '<div class="box warn"><b>부분 결과</b><ul>' + "".join(f"<li>{i}</li>" for i in items) + "</ul></div>"


def _smoke(sm: dict | None) -> str:
    if not sm:
        return ""
    rows = "".join(f"<li>{html.escape(r)}</li>" for r in sm["observations"])
    return (f'<div class="box"><b>서비스 경로 실측({html.escape(sm["recorded_at"])}, <code>api/review_judge.py</code>, '
            f'{html.escape(sm["data"])}) — {html.escape(sm["method"])}</b><ul>{rows}</ul></div>')


def render(s: dict) -> str:
    e = html.escape
    d, f = s["sets"]["demo_s300"], s["sets"]["faithful"]

    def head(data):
        return "".join(f"<th>{e(LABEL[j])}</th>" for j in data["judges"])

    def row(label, key, data, pct=False, nd=2):
        signed = key in ("mean",)                     # 평균 판정값만 부호를 붙인다
        return f"<tr><td>{e(label)}</td>" + "".join(
            f"<td>{_f(x[key], nd, pct, sign=signed)}</td>" for x in data["judges"].values()) + "</tr>"

    def cost_rows(data):
        rows = ""
        for j, x in data["judges"].items():
            rt = x.get("reasoning_tokens")
            reason = f" (추론 {rt:,})" if rt else (" (추론 토큰 미기록)" if rt is None and j != "jev" else "")
            wall = ("– (부분 결과·이어 실행 합이라 비교 불가)" if x.get("partial")
                    else f"{x['wall_s']:.0f}초 (병렬 {x['workers']})")
            rows += (f"<tr><td>{e(LABEL[j])} <span class='muted'>({x['n_judged']:,}건)</span></td><td>{wall}</td>"
                     f"<td>{x['lat_median_s']:.2f}초 / {x['lat_p95_s']:.2f}초</td><td>{x['in_tokens']:,} / {x['out_tokens']:,}{reason}</td>"
                     f"<td>${x['cost_usd']:.4f}</td><td>${x['per_1000_usd']:.4f}</td></tr>")
        return rows

    band_rows = "".join(
        f"<tr><td>{e(b['band'])}</td><td>{b['n']}</td>" + "".join(
            f"<td>{_f(f['bands'][j][i]['mean'])}</td>" for j in f["judges"]) + "</tr>"
        for i, b in enumerate(f["bands"]["llm"]))
    probe_judges = [j for j in JUDGES if any(j in v for v in s["probe"].values())]
    probe_rows = "".join(f"<tr><td>{e(k)}</td>" + "".join(f"<td>{_f(v.get(j))}</td>" for j in probe_judges) + "</tr>"
                         for k, v in s["probe"].items())
    pair = "".join(f"<tr><td>{e(LABEL[k.split('~')[0]])} ↔ {e(LABEL[k.split('~')[1]])}</td><td>{v:.2f}</td></tr>"
                   for k, v in d["pairwise_pearson"].items())
    na = {"pearson": None, "neg_recall": None, "per_1000_usd": float("nan"), "lat_median_s": float("nan"), "lat_p95_s": float("nan")}
    LL, G, L, M, J = (f["judges"].get(j, na) for j in ("llm_low", "glm_low", "llm", "glm", "jev"))
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>리뷰 판정기 비교</title>
<style>
body{{font-family:-apple-system,"Apple SD Gothic Neo","Noto Sans KR",sans-serif;background:#fafaf7;color:#1f2328;margin:0;line-height:1.7}}
main{{max-width:1000px;margin:0 auto;padding:32px 16px 64px}} h1{{font-size:1.6rem}} h2{{font-size:1.2rem;margin-top:2rem;border-left:4px solid #2563eb;padding-left:.6rem}}
table{{border-collapse:collapse;width:100%;font-size:.93rem;margin:10px 0;background:#fff}} td,th{{border:1px solid #e5e7eb;padding:6px 8px;text-align:left}}
th{{background:#f3f4f6}} .box{{background:#fff;border:1px solid #e5e7eb;border-radius:10px;padding:12px 16px;margin:12px 0}}
.warn{{background:#fffbeb;border-color:#fcd34d}} .ok{{background:#ecfdf5;border-color:#6ee7b7}} .muted{{color:#6b7280;font-size:.88rem}}
.wrap{{overflow-x:auto}}
</style></head><body><main>
<h1>리뷰 글 판정기 비교: 외부 LLM low · 사내 LLM low(GLM 5.3) · Jev</h1>
<p class="muted">실험 E4 · 코드 <code>experiments/jev/e4_judges.py</code> · 원시 결과 <code>experiments/jev/results/e4_*.json</code> ·
모든 데이터는 가상(합성)이며 사업 효과는 NOT_CALIBRATED</p>

<div class="box ok"><b>결정(사용자)</b>: 리뷰 글 판정은 선택지 없이 <b>LLM(OpenAI 호환 API)</b>으로 통일한다(2026-10-06).
LLM을 쓰는 곳의 비교는 <b>외부 AI(gpt-6-luna)와 회사가 제공하는 오픈소스 LLM(GLM 5.3)을 항상 함께</b> 재고,
<b>양쪽 모두 추론 강도 low</b>·<b>유의미한 표본 크기</b>로 잰다(2026-10-07). 사내 서버로는 측정할 수 없어
<b>사내 LLM = Z.ai 공식 API의 GLM 5.3으로 가정</b>한다(2026-10-07).</div>

<h2>1. 왜 비교했나</h2>
<p>협업 점수의 리뷰 부분은 <code>쌍 리뷰 점수 = 0.5 × 항목 점수 + 0.5 × 글 점수</code>다. 이전 기본이던 규칙 기반은
글 점수 자리에 항목 균형을 다시 넣어 평가 사유(글)를 점수에 쓰지 않았다. 글을 읽는 판정기로 외부 LLM, 사내 LLM(사내 서버로는
측정할 수 없어 Z.ai 공식 API의 GLM 5.3을 사내 LLM으로 가정 — 사용자 결정), 판단 전용 모델 Jev를 같은 글로 비교했다.</p>

<h2>2. 방법과 표본 크기</h2>
<ul>
<li><b>B. 충실한 글 {f['n']}건(정답 있음)</b> — 시연 리뷰에서 뽑은 {f['n']}건을, 고른 항목을 빠짐없이 한 문장씩 다시 쓴 글.
<b>정답 = 항목 균형</b>. 실제 부정 리뷰 {f['stats'].get('neg_n', '?')}건 → 부정 검출의 95% 구간 반폭은 최대 약 ±11%p(비율이 50% 근처일 때).</li>
<li><b>A. 시연 원문 무작위 {d['n']}건</b> — 현재 시연 묶음 리뷰 {s['demo_total']:,}건에서 무작위로 뽑은 {d['n']}건
(일치율 95% 구간 반폭 ≤ ±{1.96 * (0.25 / max(d['n'], 1)) ** 0.5 * 100:.1f}%p). 정답이 없어 분포·판정기 간 일치만 본다.</li>
<li><b>대조 문장</b> 4건 — 척도(−1~+1)가 살아 있는지.</li>
<li>두 LLM은 같은 지시문(서비스 파서)을 받는다. <b>주 비교는 양쪽 모두 추론 강도 low</b>(사용자 지시 "전부 low"):
외부 gpt-6-luna low, 사내 GLM 5.3 low(Z.ai 공식 API <code>glm-5.3</code>). 참고 열: 외부 기본 추론(지금 서비스 리뷰 판정 설정 —
<code>TEAMWEAVER_REVIEW_REASONING_EFFORT</code> 미설정), 사내 max(비용 때문에 같은 글이 남은 B·대조 문장만, 2026-10-06 측정).
Jev는 5단계 점수의 확률 기댓값을 −1~1로 옮겼다. 부호 판정의 중립 구간 ±0.1, 병렬 {WORKERS}건.</li>
<li>짝지은 비교(같은 건에 두 판정기)는 McNemar 정확검정, 비율 구간은 Wilson 95%.</li>
</ul>

{_partial_note(s)}
<h2>3. 결과</h2>
<h3>B. 충실한 글 {f['n']}건 (정답 있음)</h3>
<div class="wrap"><table><tr><th>지표</th>{head(f)}</tr>
{row("정답과 상관(피어슨)", "pearson", f)}{row("정답과 순위 상관(스피어만)", "spearman", f)}
{row("정답과 평균 절대 차이(작을수록 좋음)", "mae", f)}{row("긍정·중립·부정 일치율", "sign_agree", f, pct=True)}
{row("실제 부정 리뷰를 부정으로 잡은 비율", "neg_recall", f, pct=True)}{row("평균 판정값 (정답 평균 " + _f(f['items']['mean']) + ")", "mean", f)}
</table></div>
<p>{_stats_text(f.get("stats", {}))}<br>{_stats_text(f.get("stats_low_vs_max", {}))}</p>
<div class="wrap"><table><tr><th>정답(항목) 구간</th><th>건수</th>{head(f)}</tr>{band_rows}</table></div>

<h3>A. 시연 원문 무작위 {d['n']}건 (정답 없음)</h3>
<div class="wrap"><table><tr><th>지표</th>{head(d)}</tr>
{row("평균 판정값 (항목 균형 평균 " + _f(d['items']['mean']) + ")", "mean", d)}
{row("부정(−0.1 미만)으로 판정한 비율 (항목 기준 " + _f(d['items']['neg_share'], pct=True) + ")", "neg_share", d, pct=True)}
{row("항목 균형과 상관", "pearson", d)}{row("항목 기준 부정 리뷰를 부정으로 잡은 비율", "neg_recall", d, pct=True)}
</table></div>
<table><tr><th>판정기끼리 상관 (A)</th><th>피어슨</th></tr>{pair}</table>

<h3>대조 문장</h3>
<div class="wrap"><table><tr><th>글</th>{"".join(f"<th>{e(LABEL[j])}</th>" for j in probe_judges)}</tr>{probe_rows}</table></div>

<h2>4. 비용과 소요 시간</h2>
<div class="wrap"><table><tr><th>판정기</th><th>총 소요 시간</th><th>건당 지연 중앙값 / 95%</th><th>입력 / 출력 토큰</th><th>비용</th><th>1,000건당</th></tr>
<tr><th colspan="6">A. 시연 원문 무작위 {d['n']}건</th></tr>{cost_rows(d)}
<tr><th colspan="6">B. 충실한 글 {f['n']}건</th></tr>{cost_rows(f)}</table></div>
<p class="muted">비용 = 토큰 × 공식 단가(2026-10). gpt-6-luna 입력 $0.10·출력 $0.50, GLM 5.3(Z.ai) 입력 $1.40·캐시된 입력 $0.26·출력 $4.40
(추론 토큰은 출력에 포함), Jev 입력 $0.042·출력 무료 / 100만 토큰. <b>Z.ai를 사내 LLM으로 가정했으므로 사내 시간·비용 =
Z.ai 기준이다</b>(실제 온프렘이면 GPU 시간이 비용이 된다).</p>
{_smoke(s.get("service_smoke"))}

<h2>5. 해석</h2>
<ul>
<li><b>순위는 모두 잘 맞힌다</b>(B 상관: 외부 low {_f(LL['pearson'], sign=False)} · 사내 low {_f(G['pearson'], sign=False)} · 외부 기본 {_f(L['pearson'], sign=False)} · 사내 max {_f(M['pearson'], sign=False)} · Jev {_f(J['pearson'], sign=False)}).
차이는 <b>부정 검출</b>이다: 외부 low {_f(LL['neg_recall'], pct=True)} · 사내 low {_f(G['neg_recall'], pct=True)} · 외부 기본 {_f(L['neg_recall'], pct=True)} · 사내 max {_f(M['neg_recall'], pct=True)} · Jev {_f(J['neg_recall'], pct=True)}.</li>
<li><b>같은 low끼리 비교(주 비교)</b>: 위 3절의 검정 문장 참고. 사내 low 대 사내 max는 같은 모델 안의 추론 강도 차이다.</li>
<li><b>비용·시간</b>: 1,000건당 외부 low ${LL['per_1000_usd']:.2f} · 사내 low ${G['per_1000_usd']:.2f} · 사내 max ${M['per_1000_usd']:.2f}.
건당 지연 중앙값(재시도 대기 제외) 외부 low {LL['lat_median_s']:.1f}초 · 사내 low {G['lat_median_s']:.1f}초(95%: {LL['lat_p95_s']:.1f}초 · {G['lat_p95_s']:.1f}초).
사내 쪽 <b>총 처리 시간은 지연 중앙값보다 훨씬 길다</b>(4절 표의 총 소요 시간) — 재시도 횟수를 기록하지 않아 원인은 추정이다
(Z.ai 요청 한도 429 재시도로 보인다).</li>
<li><b>사내 설정 선택에 주는 뜻</b>: 사내 low는 max보다 약 5배 싸지만 실제 부정 리뷰를 덜 잡는다
({_f(G['neg_recall'], pct=True)} 대 max {_f(M['neg_recall'], pct=True)}). 아쉬운 점을 부드럽게 쓰는 조직이면 이 차이가 협업 점수에
그대로 들어간다 — 사내 배포의 추론 강도는 비용(GPU 시간)과 이 정확도를 함께 보고 정한다(중간 강도 high는 재지 않았다).</li>
<li><b>A에서는 모든 판정기가 긍정으로 쏠린다</b> — 판정기 문제가 아니라 시연 생성기의 글이 항목보다 부드럽기 때문이다(claude-a에 수정 요청).</li>
</ul>

<h2>6. 한계</h2>
<div class="box warn"><ul>
<li><b>결정 근거(부정 검출)는 B(생성기 문장으로 다시 쓴 글)의 결과다.</b> 정답은 "항목 균형"이고 실제 협업 성과가 아니다(NOT_CALIBRATED).</li>
<li>B의 글은 생성기의 문장 은행으로 만들었다. 실제 사람의 글(직설적인 평가 포함)과는 표현 폭이 다르다.</li>
<li>판정값이 바뀌었을 때 <b>배치 결과(협업 점수·MILP 해)에 주는 영향은 재지 않았다.</b></li>
<li><b>가정: Z.ai 공식 API의 GLM 5.3 = 사내 LLM</b>(사내 서버로는 측정할 수 없다 — 사용자 결정). 같은 공개 가중치지만 실제 온프렘
서빙(BF16/FP8, vLLM 버전)이 다르면 결과·속도가 조금 달라질 수 있다. 비용은 Z.ai 요금으로 잡았다(온프렘이면 GPU 시간이 비용이 된다).</li>
<li>GLM max 참고 열은 2026-10-06 측정(같은 글)이고, 시연 원문은 데이터가 바뀐 뒤라 max를 다시 재지 않았다.</li>
<li>이전 실험 E2(가상 fixture 266건, <code>outputs/jev-experiment.html</code>)에서도 같은 방향(LLM 부호 일치 74% 대 Jev 61%)이었다.</li>
</ul></div>
</main></body></html>"""


def main() -> None:
    s = summarize()
    REPORT.write_text(render(s), "utf-8")
    keys = ("pearson", "mae", "sign_agree", "neg_recall", "mean", "neg_share", "wall_s", "lat_median_s", "cost_usd",
            "in_tokens", "out_tokens", "reasoning_tokens")
    print(json.dumps({k: {j: {m: round(x[m], 3) if isinstance(x.get(m), float) else x.get(m) for m in keys}
                          for j, x in d["judges"].items()} for k, d in s["sets"].items()}, ensure_ascii=False, indent=1))
    print("probe", json.dumps(s["probe"], ensure_ascii=False))
    print("report:", REPORT)


if __name__ == "__main__":
    main()
