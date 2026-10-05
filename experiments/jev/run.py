"""Jev 대체 가능성 실험 실행기.

  uv run --group benchmark python -m experiments.jev.run            # 기록이 있으면 재생, 없고 키가 있으면 호출
  TYPESAFE_API_KEY=... uv run --group benchmark python -m experiments.jev.run

결과: outputs/jev-experiment.json(원 수치) + outputs/jev-experiment.html(시연용 보고서).
Jev 응답 기록: experiments/jev/cassettes/*.json(키 없이 재생 가능). 합성 데이터, NOT_CALIBRATED."""
from __future__ import annotations

import argparse
import json
import platform
import time
from pathlib import Path

from experiments.jev import e1_solver, e2_reviews, e3_swap
from experiments.jev.client import JevClient, MissingRecording, tape_summary, usage_cost_usd
from experiments.jev.common import fixture_instance, synthetic_instance

HERE = Path(__file__).parent
CASSETTES = HERE / "cassettes"


def _load_dotenv(path: Path = Path(".env")) -> None:
    """저장소 루트 .env의 KEY=값을 환경에 넣는다(이미 있는 값은 덮지 않는다). 값은 출력하지 않는다."""
    if not path.exists():
        return
    import os
    for line in path.read_text("utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _client(name: str) -> JevClient | None:
    c = JevClient(CASSETTES / f"{name}.json")
    return c if (c.can_call or c.cassette.exists()) else None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=Path("outputs/jev-experiment.json"))
    ap.add_argument("--only", choices=["e1", "e2", "e3"], action="append")
    args = ap.parse_args()
    only = set(args.only or ["e1", "e2", "e3"])
    _load_dotenv()
    started = time.monotonic()
    result = {"experiment": "jev-substitution", "business_validity": "NOT_CALIBRATED",
              "run_at": time.strftime("%Y-%m-%d %H:%M"), "platform": platform.platform(), "jev_model_requested": "jev-latest"}
    def guarded(name, fn):
        """기록이 일부만 있고 키가 없으면 그 실험만 '미완'으로 남기고 나머지는 계속한다."""
        try:
            result[name] = fn()
        except MissingRecording as exc:
            result[name + "_incomplete"] = str(exc)
        print(name, "done", flush=True)

    if "e1" in only:
        c = _client("e1")
        guarded("e1", lambda: [e1_solver.run(synthetic_instance(25, 5, 2), c), e1_solver.run(fixture_instance(), c)])
    if "e2" in only:
        guarded("e2", lambda: e2_reviews.run(_client("e2"), CASSETTES / "e2_luna.json"))
    if "e3" in only:
        guarded("e3", lambda: e3_swap.run(fixture_instance(), _client("e3")))
    result["jev_tape"] = tape_summary([CASSETTES / f"{n}.json" for n in ("e1", "e2", "e3")])
    tokens = 0
    for part in ("e1", "e2", "e3"):
        blocks = result.get(part)
        for b in (blocks if isinstance(blocks, list) else [blocks] if blocks else []):
            tokens += b.get("methods", {}).get("jev", {}).get("input_tokens", 0)
    result["jev_input_tokens"] = tokens
    result["jev_cost_usd"] = usage_cost_usd(tokens)
    result["seconds"] = time.monotonic() - started
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=1), "utf-8")
    from experiments.jev.report import render
    print("report:", render(result, args.out.with_suffix(".html")))


if __name__ == "__main__":
    main()
