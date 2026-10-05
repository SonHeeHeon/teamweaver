"""TypeSafe Jev(판단 전용 모델) 호출기 -- 기록(cassette)과 재생.

Jev는 글을 만들지 않고 질문마다 Choice(보기 중 하나·확률) / Score(등급 점수) / Noul(참일 확률)만
돌려준다(docs.typesafe.ai). SDK(`typesafe-sdk`)는 의존성을 늘리므로 쓰지 않고 HTTP API를 직접 부른다:
`POST https://api.typesafe.ai/v1/systemone`, `Authorization: Bearer $TYPESAFE_API_KEY`.

모든 응답은 요청 해시를 키로 cassette JSON에 저장한다. 그래서
- 실험을 다시 돌리면 같은 요청은 다시 부르지 않고(비용·시간 0) 기록된 답과 기록된 지연시간을 쓴다.
- 시연 때 키가 없어도 기록으로 보고서를 다시 만들 수 있다.
- 기록에 없는 요청을 키 없이 부르면 MissingRecording -- 결과를 지어내지 않는다.
API 키는 기록하지 않는다."""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

URL = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-latest"
INPUT_USD_PER_1M = 0.042            # 공개 단가(2026-10): 입력 $0.042/100만 토큰, 출력 무료


class MissingRecording(RuntimeError):
    """키 없이 기록에 없는 요청을 불렀다."""


@dataclass(frozen=True)
class JevAnswer:
    answers: dict                   # 질문 키 -> {"type", "choice"/"score"/"noul", "confidence", "probabilities"}
    model: str
    latency_s: float
    input_tokens: int
    replayed: bool


def choice(instructions: str, options: dict[str, str]) -> dict:
    return {"type": "choice", "instructions": instructions, "criteria": options}


def score(instructions: str, levels: list[str]) -> dict:
    return {"type": "score", "instructions": instructions, "criteria": levels}


def noul(instructions: str) -> dict:
    return {"type": "noul", "instructions": instructions}


class JevClient:
    def __init__(self, cassette: Path, *, api_key: str | None = None, model: str = MODEL,
                 transport: httpx.BaseTransport | None = None, timeout_s: float = 30.0):
        self.cassette = cassette
        self.model = model
        self.api_key = api_key if api_key is not None else os.environ.get("TYPESAFE_API_KEY") or None
        self._tape: dict = json.loads(cassette.read_text("utf-8")) if cassette.exists() else {}
        self._http = httpx.Client(transport=transport, timeout=timeout_s)
        self.live_calls = 0

    @property
    def can_call(self) -> bool:
        return self.api_key is not None

    def _key(self, body: dict) -> str:
        return hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True).encode()).hexdigest()

    def ask(self, state: str, questions: dict) -> JevAnswer:
        body = {"model": self.model, "state": state, "questions": questions}
        key = self._key(body)
        if key in self._tape:
            rec = self._tape[key]
            return JevAnswer(rec["answers"], rec["model"], rec["latency_s"], rec["input_tokens"], True)
        if not self.can_call:
            raise MissingRecording("TYPESAFE_API_KEY가 없고 이 요청의 기록도 없다")
        res, attempts, last_attempt = None, 0, 0.0
        for attempt in range(4):                   # 429·5xx·연결 오류만 재시도(지수 대기)
            attempts += 1
            t = time.monotonic()
            try:
                res = self._http.post(URL, json=body, headers={"Authorization": f"Bearer {self.api_key}"})
            except httpx.TransportError:
                if attempt == 3:
                    raise
                time.sleep(2 ** attempt)
                continue
            last_attempt = time.monotonic() - t
            if res.status_code not in (429, 500, 502, 503, 504) or attempt == 3:
                break
            time.sleep(2 ** attempt)
        res.raise_for_status()
        data = res.json()
        _check_answers(questions, data.get("answers"))
        # 지연시간은 마지막 시도의 왕복만 -- 재시도 대기는 섞지 않는다(시도 횟수는 따로 남긴다).
        latency = last_attempt
        rec = {"answers": data["answers"], "model": data.get("model", self.model), "latency_s": latency,
               "attempts": attempts, "input_tokens": int(data.get("usage", {}).get("input_tokens", 0)),
               "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
        self._tape[key] = rec
        self.live_calls += 1
        self._save()
        return JevAnswer(rec["answers"], rec["model"], latency, rec["input_tokens"], False)

    def _save(self) -> None:
        self.cassette.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.cassette.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._tape, ensure_ascii=False, indent=1, sort_keys=True), "utf-8")
        tmp.replace(self.cassette)


def _check_answers(questions: dict, answers) -> None:
    """응답이 물은 질문마다 같은 종류의 답을 담았는지 확인한다 -- 빠지거나 다르면 조용히 넘기지 않는다."""
    if not isinstance(answers, dict):
        raise ValueError(f"Jev 응답에 answers가 없다: {answers!r}")
    for key, q in questions.items():
        a = answers.get(key)
        if not isinstance(a, dict) or a.get("type", q["type"]) != q["type"] or q["type"] not in a:
            raise ValueError(f"Jev 응답의 {key!r} 답이 질문({q['type']})과 맞지 않는다: {a!r}")


def tape_summary(cassettes: list[Path]) -> dict:
    """기록에 담긴 실제 모델 버전과 기록 시각 범위 -- 'jev-latest'가 무엇이었는지 보고서에 밝힌다."""
    models, times, n = set(), [], 0
    for c in cassettes:
        if not c.exists():
            continue
        for rec in json.loads(c.read_text("utf-8")).values():
            if "answers" not in rec or "recorded_at" not in rec:
                continue
            n += 1
            models.add(rec.get("model"))
            times.append(rec["recorded_at"])
    return {"records": n, "models": sorted(m for m in models if m), "recorded_from": min(times) if times else None,
            "recorded_to": max(times) if times else None}


def usage_cost_usd(input_tokens: int) -> float:
    return input_tokens * INPUT_USD_PER_1M / 1_000_000
