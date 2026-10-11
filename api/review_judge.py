"""리뷰 글 판정: 평가 사유(글)를 LLM이 읽어 글 극성(text_polarity)을 매긴다(사용자 결정 2026-10-06, 선택지 없음).

왜: 쌍 리뷰 점수 = 0.5×항목 점수 + 0.5×글 극성인데, 실데이터 형식(CSV 묶음)의 기존 규칙 기반 파서는 글 극성 자리에
항목 균형을 다시 넣어 평가 사유를 점수에 전혀 쓰지 않았다. 판정기 비교 실험 E4(`experiments/jev/e4_judges.py`,
`outputs/review-judge-comparison.html`)에서 LLM(gpt-6-luna)은 항목을 충실히 쓴 글의 부정 리뷰를 92% 잡았고
Jev는 17%였다 -- 그래서 LLM으로 통일했다(Jev·규칙 기반 선택지는 없앴다).

접속은 OpenAI 호환 Chat Completions API다. 주소 `TEAMWEAVER_REVIEW_BASE_URL`(없으면 OpenAI), 모델 `TEAMWEAVER_REVIEW_MODEL`
(없으면 pricing의 parse_model). 키: OpenAI 주소면 `OPENAI_API_KEY`, 다른 주소면 `TEAMWEAVER_REVIEW_API_KEY`만 쓴다
(OpenAI 키를 사내 서버로 보내지 않게, 리뷰 S3; 사내 LLM이 키 없이 열려 있으면 비워 둔다). 사내 온프렘 LLM(vLLM·Ollama
등)은 주소만 바꾸면 같은 코드로 쓴다. 주소가 사내인지(localhost·사설 IP·.local/.internal 또는
`TEAMWEAVER_REVIEW_ONPREM=1`) OpenAI인지 그 밖(확인 안 됨)인지를 화면에 알린다(리뷰 S2).
실데이터(synthetic이 true가 아님)를 사내가 아닌 곳으로 보내려면 `TEAMWEAVER_REVIEW_ALLOW_EXTERNAL=1`이 있어야 한다 --
없으면 판정하지 않고 항목 점수를 쓴다(리뷰 S4, 동의 없는 외부 전송 방지).
지시문은 가상 데이터 생성 때의 LLM 파서(`core/datagen/parse_reviews._SYSTEM`)와 같다(실험 E2·E4와 같은 조건).

같은 글은 다시 부르지 않도록 (모델·주소·지시문·글) 해시 → 극성을 디스크 캐시(0600)에 둔다. 캐시에는 지금 데이터의
판정만 남긴다. 원문·API 키는 어디에도 기록하지 않는다."""
from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
import math
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout, as_completed
from pathlib import Path
from urllib.parse import urlparse

import httpx

from core.datagen.parse_reviews import _SYSTEM as INSTRUCTION
from core.domain.models import Dataset, ParsedReview

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.openai.com/v1"
KEY_ENV = "OPENAI_API_KEY"
OTHER_KEY_ENV = "TEAMWEAVER_REVIEW_API_KEY"   # OpenAI가 아닌 주소(사내 LLM 등)의 키
ONPREM_ENV = "TEAMWEAVER_REVIEW_ONPREM"       # 1이면 주소를 사내로 본다(관리자 명시)
ALLOW_EXTERNAL_ENV = "TEAMWEAVER_REVIEW_ALLOW_EXTERNAL"   # 1이면 실데이터도 사내가 아닌 곳으로 보낸다
# 판정 전용 주소 변수다(OPENAI_BASE_URL이 아니다): openai SDK는 OPENAI_BASE_URL을 스스로 읽으므로, 그것을 바꾸면
# 브리핑·설명 클라이언트도 OPENAI_API_KEY를 사내 서버로 보낸다(리뷰 2라운드 S-1).
BASE_URL_ENV = "TEAMWEAVER_REVIEW_BASE_URL"
MODEL_ENV = "TEAMWEAVER_REVIEW_MODEL"
# 추론 강도(예: Z.ai GLM 5.3은 low|high|max). 기본 low(사용자 결정 2026-10-07, 아래 DEFAULT_REASONING), none|off면
# 보내지 않는다. 허용값은 서버마다 다르다 -- vLLM·SGLang은 대개 low|medium|high만 받고, 추론 모델이 아닌 OpenAI 모델은
# 이 칸 자체를 거절(400)한다. 사내 배포가 받는 값으로 둔다. 시간 한도는 아래 judge_reviews가 강도·주소에 맞춰 늘린다.
REASONING_ENV = "TEAMWEAVER_REVIEW_REASONING_EFFORT"
WORKERS_ENV = "TEAMWEAVER_REVIEW_WORKERS"
CONVERSION = "llm-text-polarity-v1"   # 바꾸면 캐시 키가 달라져 이전 판정을 재사용하지 않는다
WORKERS = 16                        # 실측: 32는 OpenAI 요청 한도(429)에 걸렸고 16은 1,372건을 끝냈다(E4)
DEADLINE_S = 600.0                  # 데이터셋 하나를 판정하는 전체 한도(실측 100명 1,372건 병렬 16: 약 4분)
REQUEST_TIMEOUT_S = 60.0
FORMAT_ATTEMPTS = 3
ATTEMPTS = 6                        # 요청 한도(429)는 잠깐 기다리면 풀린다 -- 서버가 알려 준 만큼 기다려 다시 보낸다
MAX_WAIT_S = 20.0
_RETRY_STATUS = (429, 500, 502, 503, 504)
_cache_lock = threading.Lock()


class JudgeError(RuntimeError):
    """LLM 판정을 끝내지 못했다(키·주소·네트워크·응답 형식·시간 초과). 메시지에 키·원문을 담지 않는다.
    retry=True면 같은 요청을 다시 보내 볼 만한 오류(응답 형식·일시 오류)다."""

    def __init__(self, msg: str, *, retry: bool = False):
        super().__init__(msg)
        self.retry = retry


_OPENAI_HOST = "api.openai.com"


def _is_openai(url: str) -> bool:
    return urlparse(url).hostname == _OPENAI_HOST


def api_key(url: str | None = None) -> str | None:
    """OpenAI 주소면 OPENAI_API_KEY, 아니면 판정 전용 키만(OpenAI 키를 다른 서버로 보내지 않는다)."""
    url = (url or base_url()).rstrip("/")
    return os.environ.get(KEY_ENV if _is_openai(url) else OTHER_KEY_ENV) or None


def base_url() -> str:
    return (os.environ.get(BASE_URL_ENV) or DEFAULT_BASE_URL).rstrip("/")


def model() -> str:
    if os.environ.get(MODEL_ENV):
        return os.environ[MODEL_ENV]
    from core.config import load_pricing
    return load_pricing()["parse_model"]


def _is_internal_host(host: str) -> bool:
    if host in ("localhost",) or host.endswith((".local", ".internal", ".localhost")):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback


DEFAULT_REASONING = "low"           # 사용자 결정(2026-10-07): 사내 LLM(GLM 5.3) 추론 강도 low. 외부 gpt-6-luna도 low를 받는다(E4 실측)
_SLOW_EFFORTS = ("high", "xhigh", "max")   # 건당 10초 이상(E4 GLM max 실측 중앙값 10.5초, 최대 70초)


def reasoning_effort() -> str | None:
    """기본 low. 서버가 이 칸을 받지 않는 모델이면 TEAMWEAVER_REVIEW_REASONING_EFFORT=none(또는 off)으로 끈다.
    소문자로 맞춘다 -- 같은 강도가 대소문자로 캐시 키가 갈리지 않게. 비었거나 공백뿐이면 미설정(low)으로 본다
    (.env의 `KEY= ` 실수로 조용히 꺼지지 않게). none은 끄는 값으로 예약해 OpenAI의 reasoning_effort="none"은 보낼 수 없다."""
    value = (os.environ.get(REASONING_ENV) or "").strip().lower() or DEFAULT_REASONING
    return None if value in ("none", "off") else value


def _per_review_s(effort: str | None, url: str) -> float:
    """전체 한도 계산용 병렬 한 줄당 건당 시간(E4 실측, 병렬 16).
    - 강한 추론(high·xhigh·max)은 주소와 상관없이 길다: GLM max 300건 265초(한 줄당 약 14초), OpenAI 강한 추론은 미측정.
    - 사내(OpenAI가 아닌 주소)에 추론을 켜면 GLM low도 요청 한도 대기로 300건에 213~333초(한 줄당 최대 약 17.7초).
    둘 다 18초(× 여유 2배 = 36초). 사내 실제 서버가 생기면 다시 잰다.
    - OpenAI low·추론 끔은 건당 약 2초(E4 중앙값 2.0·2.15초, 300건 41~45초)."""
    if effort in _SLOW_EFFORTS or (effort and not _is_openai(url)):
        return 18.0
    return 2.25


def endpoint() -> dict:
    """화면 안내용: 글이 어디로 가는가.
    location: "openai"(회사 밖) | "onprem"(사내로 확인: 사설 주소·관리자 명시) | "unknown"(사내인지 확인 안 됨).
    external: 회사 밖일 수 있는가(openai·unknown)."""
    url = base_url()
    host = urlparse(url).hostname or url
    if _is_openai(url):
        location = "openai"
    elif os.environ.get(ONPREM_ENV) == "1" or _is_internal_host(host):
        location = "onprem"
    else:
        location = "unknown"
    return {"host": host, "location": location, "external": location != "onprem", "model": model(),
            # 실데이터도 회사 밖일 수 있는 곳으로 보내도록 서버가 허용했는가(화면이 동의 상태를 보인다, 리뷰 2라운드 S-3)
            "external_allowed": external_allowed()}


def external_allowed() -> bool:
    return os.environ.get(ALLOW_EXTERNAL_ENV) == "1"


def _workers() -> int:
    try:
        return max(1, int(os.environ.get(WORKERS_ENV, WORKERS)))
    except ValueError:
        return WORKERS


def _cache_key(pos: str, neg: str, model_name: str, url: str, effort: str | None = None) -> str:
    # 추론 강도는 보낼 때만 키에 넣는다 -- none이면 effort 도입 전 키(와 쌓인 판정·데이터 버전)와 같다(리뷰 MUST).
    # 서비스 기본이 2026-10-07부터 low라 기본 경로의 키는 그때 한 번 바뀌었다(미리 계산 결과도 다시 만든다).
    parts = [model_name, url, INSTRUCTION, CONVERSION] + ([effort] if effort else []) + [pos, neg]
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode("utf-8")).hexdigest()


def to_polarity(content: str) -> float:
    """LLM 응답 본문(JSON) → [-1, 1]. 오류 메시지에는 응답 내용을 넣지 않는다(원문이 섞여 와도 화면에 새지 않게)."""
    try:
        raw = json.loads(content).get("text_polarity")
    except (ValueError, AttributeError, TypeError) as exc:
        raise JudgeError("LLM 응답이 JSON 객체가 아니다", retry=True) from exc
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not math.isfinite(raw):
        raise JudgeError("LLM 응답에 숫자 text_polarity가 없다", retry=True)
    return max(-1.0, min(1.0, float(raw)))


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
        log.warning("리뷰 판정 캐시를 저장하지 못했다(%s): %s", path, exc)


def clear_cache(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        log.warning("리뷰 판정 캐시를 지우지 못했다(%s): %s", path, exc)


def _retry_wait(res: httpx.Response, attempt: int) -> float:
    """서버가 알려 준 대기 시간(retry-after-ms·Retry-After)을 따르고, 없으면 지수 대기. 최대 MAX_WAIT_S."""
    wait = 2 ** attempt * 0.5
    ms = res.headers.get("retry-after-ms", "")
    sec = res.headers.get("Retry-After", "")
    try:
        if ms:
            wait = float(ms) / 1000
        elif sec:
            wait = float(sec)
    except ValueError:
        pass
    return min(max(wait, 0.1), MAX_WAIT_S)


def _ask(http: httpx.Client, url: str, key: str | None, model_name: str, pos: str, neg: str,
         stop: threading.Event, effort: str | None = None) -> float:
    body = {"model": model_name, "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": INSTRUCTION},
                         {"role": "user", "content": json.dumps({"좋은점": pos, "나쁜점": neg}, ensure_ascii=False)}]}
    if effort:
        body["reasoning_effort"] = effort
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    res = None
    for attempt in range(ATTEMPTS):
        if stop.is_set():
            raise JudgeError("다른 요청이 실패해 판정을 멈췄다")
        try:
            res = http.post(url, json=body, headers=headers)
        except httpx.TransportError as exc:
            if attempt == ATTEMPTS - 1:
                raise JudgeError(f"LLM API에 연결하지 못했다: {type(exc).__name__}") from None
            _wait(min(2 ** attempt * 0.5, MAX_WAIT_S), stop)
            continue
        if res.status_code not in _RETRY_STATUS or attempt == ATTEMPTS - 1:
            break
        _wait(_retry_wait(res, attempt), stop)
    if res.status_code in (401, 403):
        raise JudgeError(f"LLM API가 키를 거절했다(HTTP {res.status_code})")
    if res.status_code != 200:
        hint = (f" -- 추론 강도 칸을 받지 않는 모델이면 {REASONING_ENV}=none"
                if effort and res.status_code == 400 else "")
        raise JudgeError(f"LLM API 오류(HTTP {res.status_code}{_error_code(res)}){hint}",
                         retry=res.status_code == 400)
    try:
        content = res.json()["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise JudgeError("LLM 응답 형식이 예상과 다르다", retry=True) from exc
    if not isinstance(content, str):
        raise JudgeError("LLM 응답 형식이 예상과 다르다(본문 없음 -- 거절·필터 가능)", retry=True)
    return to_polarity(content)


def _ask_once_more(http, url, key, model_name, pos, neg, stop, effort=None) -> float:
    """응답 형식 오류·일시적 400은 같은 요청을 두 번까지 다시 보낸다 -- 한 건 때문에 데이터 전체가 실패하지 않게(리뷰 S7)."""
    for attempt in range(FORMAT_ATTEMPTS):
        try:
            return _ask(http, url, key, model_name, pos, neg, stop, effort)
        except JudgeError as exc:
            if not exc.retry or attempt == FORMAT_ATTEMPTS - 1 or stop.is_set():
                raise
    raise AssertionError("unreachable")


def _wait(seconds: float, stop: threading.Event) -> None:
    """기다리되 다른 요청이 실패해 멈추면 바로 깬다(남은 스레드가 오래 살지 않게)."""
    stop.wait(seconds)


def _error_code(res: httpx.Response) -> str:
    """원인 진단용 오류 코드만(본문·원문은 넣지 않는다). 예: model_not_found."""
    try:
        err = res.json().get("error")
        code = err.get("code") or err.get("type") if isinstance(err, dict) else None
    except (ValueError, AttributeError):
        return ""
    return f", {code}" if isinstance(code, str) and code.replace("_", "").isalnum() and len(code) <= 60 else ""


def cache_keys(ds: Dataset, url: str | None = None, model_name: str | None = None) -> list[str]:
    """이 데이터의 리뷰마다 판정 캐시 키(지금 판정기: 주소·모델·추론 강도). 시연 판정 내보내기(scripts)도 같은 키를 쓴다."""
    url = (url or base_url()).rstrip("/")
    model_name = model_name or model()
    effort = reasoning_effort()
    return [_cache_key(r.positive.text, r.negative.text, model_name, url, effort) for r in ds.reviews]


def judge_reviews(ds: Dataset, parsed: list[ParsedReview], *, cache_path: Path, key: str | None = None,
                  url: str | None = None, model_name: str | None = None,
                  transport: httpx.BaseTransport | None = None, workers: int | None = None,
                  deadline_s: float | None = None, trim: bool = True,
                  seed_path: Path | None = None) -> list[ParsedReview]:
    """parsed의 text_polarity만 LLM 판정으로 바꾼 새 목록. 하나라도 실패하거나 시간 한도를 넘으면
    JudgeError(부분 적용 없음). 이미 받은 판정은 캐시에 남긴다.
    trim=True면 캐시에 지금 데이터의 판정만 남긴다(실데이터). 가상 데이터는 trim=False로 쌓아 둔다 -- 업로드 후
    되돌려도 시연 판정을 다시 부르지 않고 값·버전이 그대로다(리뷰 2라운드 S-2).
    seed_path: 저장소에 동봉한 판정(가상 시연 묶음, `demo/review_judgments.json`). 이 데이터의 키는 동봉 값이 캐시보다 우선한다 --
    LLM은 같은 글도 매번 조금씩 다르게 매기므로, 다른 기기·빈 데이터 폴더에서도 미리 계산 때와 같은 판정값(= 같은
    데이터 버전)을 쓰게 한다(사용자 요청 2026-10-11). 판정기가 다르면 키가 달라 쓰이지 않는다."""
    url = (url or base_url()).rstrip("/")
    key = key if key is not None else api_key(url)
    model_name = model_name or model()
    effort = reasoning_effort()
    if len(parsed) != len(ds.reviews) or any(
            p.reviewer_id != r.reviewer_id or p.reviewee_id != r.reviewee_id for p, r in zip(parsed, ds.reviews)):
        raise JudgeError("리뷰와 파싱 결과의 순서가 맞지 않아 판정을 바꿔 끼울 수 없다")
    texts = [(r.positive.text, r.negative.text) for r in ds.reviews]
    keys = [_cache_key(p, n, model_name, url, effort) for p, n in texts]
    current = set(keys)
    with _cache_lock:
        cache = _load_cache(cache_path)
        if trim and set(cache) - current:
            # 지금 데이터의 판정만 남긴다(이전 데이터의 글 해시를 쌓지 않는다) -- 일찍 실패하는 경로에서도.
            cache = {k: v for k, v in cache.items() if k in current}
            _save_cache(cache_path, cache)
        if seed_path is not None:
            # 동봉 판정이 우선한다 -- 이 기기에 같은 키의 다른 값(LLM이 따로 매긴 값)이 있어도 바꾼다(Codex 리뷰 2라운드 P2)
            seeded = {k: v for k, v in _load_cache(seed_path).items() if k in current and cache.get(k) != v}
            if seeded:
                cache.update(seeded)
                _save_cache(cache_path, cache)
    todo = {k: t for k, t in zip(keys, texts) if k not in cache}
    fresh: dict[str, float] = {}
    if todo and not key and _is_openai(url):
        raise JudgeError(f"{KEY_ENV}가 설정되지 않았다(사내 LLM이면 {BASE_URL_ENV}로 주소를 지정)")
    if todo:
        stop = threading.Event()
        # 강한 추론은 건당 지연이 길다(E4 GLM max 실측 최대 약 70초) -- 시간 초과로 같은 요청을 두 번 내지 않게 늘린다.
        # low는 최대 약 11초(E4 GLM low)·4.4초(gpt-6-luna low)라 기본 60초로 충분하다.
        http = httpx.Client(transport=transport, timeout=REQUEST_TIMEOUT_S * (3 if effort in _SLOW_EFFORTS else 1))
        n_workers = workers or _workers()
        if deadline_s is None:
            # 리뷰 수에 비례(병렬 한 줄당 건당 시간 × 여유 2배), 최소 DEADLINE_S. 300명(약 4,000건)도 한도에 안 걸리게.
            per_review_s = _per_review_s(effort, url)
            deadline_s = max(DEADLINE_S, len(todo) / n_workers * per_review_s * 2)
        pool = ThreadPoolExecutor(max_workers=n_workers)
        futures: dict = {}
        try:
            futures = {pool.submit(_ask_once_more, http, f"{url}/chat/completions", key, model_name, p, n, stop, effort): k
                       for k, (p, n) in todo.items()}
            try:
                for f in as_completed(futures, timeout=deadline_s):
                    fresh[futures[f]] = f.result()      # 첫 실패에서 멈춘다
            except FutureTimeout:
                raise JudgeError(f"LLM 판정이 {deadline_s:.0f}초 안에 끝나지 않았다"
                                 f"({len(fresh)}/{len(todo)}건 완료)") from None
        finally:
            stop.set()
            # 첫 실패 뒤에 이미 끝난 요청의 결과도 모은다 -- 다시 시도할 때 덜 부른다.
            for f, k in futures.items():
                if k not in fresh and f.done() and not f.cancelled() and f.exception() is None:
                    fresh[k] = f.result()
            # 아직 시작하지 않은 요청은 취소하고, 실행 중인 요청은 기다리지 않는다(잠금을 오래 쥐지 않게).
            pool.shutdown(wait=False, cancel_futures=True)
            http.close()
            if fresh:
                with _cache_lock:
                    merged = {**_load_cache(cache_path), **fresh}
                    if trim:
                        merged = {k: v for k, v in merged.items() if k in current}
                    _save_cache(cache_path, merged)
            cache.update(fresh)
    return [p.model_copy(update={"text_polarity": cache[k]}) for p, k in zip(parsed, keys)]


def judged_version(version: str, model_name: str, parsed: list[ParsedReview]) -> str:
    """LLM 판정값까지 버전에 넣는다 -- 다시 판정한 값이 다르면(캐시 손실·모델 변경) 다른 데이터로 구분한다."""
    h = hashlib.sha256(f"{version}|review_judge=llm|{model_name}|{CONVERSION}".encode())
    for p in parsed:
        h.update(f"|{p.text_polarity!r}".encode())
    return h.hexdigest()
