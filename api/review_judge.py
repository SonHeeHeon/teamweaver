"""리뷰 글 판정 방식(사용자 결정 2026-10-06): 규칙 기반(기본) 또는 Jev.

규칙 기반은 core.ingest/datagen이 만든 `ParsedReview.text_polarity`를 그대로 쓴다(외부 전송 없음).
Jev는 리뷰의 좋은점·나쁜점 **원문을 TypeSafe Jev API로 보내** 글 극성을 다시 판정한다 -- 외부 전송이
허용된 조직만 고르는 선택지다. 판정 기준·척도 변환은 실험 E2(experiments/jev/e2_reviews.py)와 같다:
Score 5단계 → 확률 기댓값(0..4) → [-1, 1]. 항목 기반 점수·근거 문장은 건드리지 않는다.

실측(시연 묶음 100명, 1,372건, jev-1.13.0): 첫 판정 17.4초, 캐시 0.01초. 규칙 기반과 상관 0.88이지만
평균 0.56 대 0.16으로 전반적으로 더 긍정적이고 음수 판정이 없었다 -- 협업 점수가 일괄로 오른다(화면 안내).

같은 글은 다시 부르지 않도록 (모델·지시문·변환 규칙·글) 해시 → 극성을 디스크 캐시(0600)에 둔다. 캐시에는
지금 데이터의 판정만 남긴다(다른 데이터의 해시를 쌓지 않는다). 원문·API 키는 어디에도 기록하지 않는다."""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout, as_completed
from pathlib import Path

import httpx

from core.domain.models import Dataset, ParsedReview

log = logging.getLogger(__name__)

URL = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-latest"
KEY_ENV = "TYPESAFE_API_KEY"
INSTRUCTION = "피어리뷰 좋은점/나쁜점 서술 전체의 감성 강도"
LEVELS = ["매우 부정적", "다소 부정적", "중립", "다소 긍정적", "매우 긍정적"]
# 극성 변환 규칙의 판. 바꾸면 캐시 키가 달라져 이전 판정을 재사용하지 않는다.
CONVERSION = "expected-level-v1"
WORKERS = 16
DEADLINE_S = 120.0                  # 데이터셋 하나를 판정하는 전체 시간 한도(재구성 잠금을 오래 쥐지 않게)
REQUEST_TIMEOUT_S = 15.0
_RETRY_STATUS = (429, 500, 502, 503, 504)
_cache_lock = threading.Lock()


class JevJudgeError(RuntimeError):
    """Jev 판정을 끝내지 못했다(키 없음·인증·네트워크·응답 형식·시간 초과). 메시지에 키·원문을 담지 않는다."""


def api_key() -> str | None:
    return os.environ.get(KEY_ENV) or None


def _state(review) -> str:
    return f"피어리뷰 -- 좋은점: {review.positive.text}\n나쁜점: {review.negative.text}"


def _cache_key(state: str, model: str) -> str:
    return hashlib.sha256(json.dumps([model, INSTRUCTION, LEVELS, CONVERSION, state], ensure_ascii=False)
                          .encode("utf-8")).hexdigest()


def to_polarity(answer: dict) -> float:
    """Score 답 하나 → [-1, 1]. 등급별 확률("0"~"4", 합≈1)이 있으면 기댓값, 없으면 score. 척도가 이상하면 멈춘다.
    오류 메시지에는 응답 내용을 넣지 않는다(형식이 바뀌어 원문이 섞여 와도 화면에 새지 않게)."""
    raw = answer.get("score") if isinstance(answer, dict) else None
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not math.isfinite(raw):
        raise JevJudgeError("Jev 응답에 숫자 score가 없다")
    s = float(raw)
    top = len(LEVELS) - 1
    if not 0.0 <= s <= top:
        raise JevJudgeError(f"Jev score가 예상 척도 0..{top} 밖이다")
    level = s
    probs = answer.get("probabilities")
    if isinstance(probs, dict) and set(probs) == {str(i) for i in range(len(LEVELS))}:
        p = [probs[str(i)] for i in range(len(LEVELS))]
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0 for v in p):
            raise JevJudgeError("Jev 확률 값이 올바른 숫자가 아니다")
        if abs(sum(p) - 1.0) > 0.02:
            raise JevJudgeError("Jev 확률의 합이 1이 아니다")
        expected = sum(i * v for i, v in enumerate(p))
        if abs(expected - s) > 1.0:
            raise JevJudgeError("Jev score와 확률 기댓값의 척도가 다르다")
        level = expected
    return max(-1.0, min(1.0, level / top * 2 - 1))


def _load_cache(path: Path) -> dict[str, float]:
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: float(v) for k, v in data.items()
            if isinstance(v, (int, float)) and not isinstance(v, bool) and -1.0 <= v <= 1.0}


def _save_cache(path: Path, cache: dict[str, float]) -> None:
    """실패해도 판정은 유효하다 -- 경고만 남긴다(다음엔 다시 부를 뿐이다)."""
    from api.storage import atomic_write
    try:
        atomic_write(path, json.dumps(cache, sort_keys=True).encode("utf-8"))
    except OSError as exc:
        log.warning("Jev 판정 캐시를 저장하지 못했다(%s): %s", path, exc)


def clear_cache(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        log.warning("Jev 판정 캐시를 지우지 못했다(%s): %s", path, exc)


def _ask(http: httpx.Client, key: str, state: str, model: str, stop: threading.Event) -> float:
    body = {"model": model, "state": state,
            "questions": {"polarity": {"type": "score", "instructions": INSTRUCTION, "criteria": LEVELS}}}
    res = None
    for attempt in range(3):
        if stop.is_set():
            raise JevJudgeError("다른 요청이 실패해 판정을 멈췄다")
        try:
            res = http.post(URL, json=body, headers={"Authorization": f"Bearer {key}"})
        except httpx.TransportError as exc:
            if attempt == 2:
                raise JevJudgeError(f"Jev API에 연결하지 못했다: {type(exc).__name__}") from None
            time.sleep(2 ** attempt * 0.5)
            continue
        if res.status_code not in _RETRY_STATUS or attempt == 2:
            break
        wait = 2 ** attempt * 0.5
        retry_after = res.headers.get("Retry-After", "")
        if retry_after.isdigit():
            wait = min(float(retry_after), 10.0)
        time.sleep(wait)
    if res.status_code in (401, 403):
        raise JevJudgeError(f"Jev API가 키를 거절했다(HTTP {res.status_code})")
    if res.status_code != 200:
        raise JevJudgeError(f"Jev API 오류(HTTP {res.status_code})")
    try:
        answer = res.json()["answers"]["polarity"]
    except (ValueError, KeyError, TypeError) as exc:
        raise JevJudgeError("Jev 응답 형식이 예상과 다르다") from exc
    if not isinstance(answer, dict):
        raise JevJudgeError("Jev 응답 형식이 예상과 다르다")
    return to_polarity(answer)


def judge_reviews(ds: Dataset, parsed: list[ParsedReview], *, cache_path: Path, key: str | None = None,
                  model: str = MODEL, transport: httpx.BaseTransport | None = None,
                  workers: int = WORKERS, deadline_s: float = DEADLINE_S) -> list[ParsedReview]:
    """parsed의 text_polarity만 Jev 판정으로 바꾼 새 목록. 하나라도 실패하거나 시간 한도를 넘으면
    JevJudgeError(부분 적용 없음). 이미 받은 판정은 캐시에 남긴다."""
    key = key or api_key()
    if not key:
        raise JevJudgeError(f"{KEY_ENV}가 설정되지 않았다")
    if len(parsed) != len(ds.reviews) or any(
            p.reviewer_id != r.reviewer_id or p.reviewee_id != r.reviewee_id for p, r in zip(parsed, ds.reviews)):
        raise JevJudgeError("리뷰와 파싱 결과의 순서가 맞지 않아 판정을 바꿔 끼울 수 없다")
    states = [_state(r) for r in ds.reviews]
    keys = [_cache_key(s, model) for s in states]
    with _cache_lock:
        cache = _load_cache(cache_path)
    todo = {k: s for k, s in zip(keys, states) if k not in cache}
    fresh: dict[str, float] = {}
    if todo:
        stop = threading.Event()
        http = httpx.Client(transport=transport, timeout=REQUEST_TIMEOUT_S)
        pool = ThreadPoolExecutor(max_workers=max(1, workers))
        try:
            futures = {pool.submit(_ask, http, key, s, model, stop): k for k, s in todo.items()}
            try:
                for f in as_completed(futures, timeout=deadline_s):
                    fresh[futures[f]] = f.result()      # 첫 실패에서 멈춘다
            except FutureTimeout:
                raise JevJudgeError(f"Jev 판정이 {deadline_s:.0f}초 안에 끝나지 않았다"
                                    f"({len(fresh)}/{len(todo)}건 완료)") from None
        finally:
            stop.set()
            # 아직 시작하지 않은 요청은 취소하고, 실행 중인 요청은 기다리지 않는다(잠금을 오래 쥐지 않게).
            # 연결도 닫는다 -- 실행 중인 요청은 오류로 빨리 끝나고 그 결과는 아무도 읽지 않는다.
            pool.shutdown(wait=False, cancel_futures=True)
            http.close()
            if fresh:
                with _cache_lock:
                    # 지금 데이터의 판정만 남긴다 -- 이전 데이터(예: 지난 업로드)의 해시를 쌓지 않는다.
                    current = set(keys)
                    merged = {k: v for k, v in {**_load_cache(cache_path), **fresh}.items() if k in current}
                    _save_cache(cache_path, merged)
            cache.update(fresh)
    else:
        with _cache_lock:
            if set(_load_cache(cache_path)) - set(keys):
                _save_cache(cache_path, {k: cache[k] for k in keys if k in cache})
    return [p.model_copy(update={"text_polarity": cache[k]}) for p, k in zip(parsed, keys)]


def judged_version(version: str, judge: str, parsed: list[ParsedReview] | None = None) -> str:
    """판정 방식이 바뀌면 협업 점수가 바뀌므로 다른 데이터로 구분한다. rule은 원래 버전 그대로(호환).
    jev는 실제로 받은 극성 값까지 버전에 넣는다 -- 캐시가 사라져 다시 판정한 값이 다르면 다른 버전이다(리뷰 M1)."""
    if judge == "rule":
        return version
    h = hashlib.sha256(f"{version}|review_judge={judge}|{CONVERSION}".encode())
    for p in parsed or []:
        h.update(f"|{p.text_polarity!r}".encode())
    return h.hexdigest()
