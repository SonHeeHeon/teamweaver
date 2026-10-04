"""서버가 계산한 플랜임을 보이는 서명(K10, Codex 2라운드 지적).

서버는 플랜을 저장하지 않는다(stateless). 대신 /api/optimize가 플랜마다 HMAC 토큰을
붙이고, PDF 요청은 원 플랜과 함께 그 토큰을 돌려보낸다. 토큰은 (데이터셋 버전, 라벨,
명단, 가중치, 적용 MILP 파라미터)에 묶인다 -- 하나라도 바꾸면 검증에 실패한다.

비밀키는 TEAMWEAVER_PLAN_SECRET이 있으면 그것, 없으면 프로세스 시작 때 무작위로 만든다
(재기동하면 이전 토큰은 무효 -- 그때의 PDF는 '미검증'으로 표시된다).
"""
import hashlib
import hmac
import json
import os
import secrets

from core.optimize.milp import MilpParams

_SECRET = (os.environ.get("TEAMWEAVER_PLAN_SECRET", "").encode()
           or secrets.token_bytes(32))


def _canonical(dataset_version: str, label: str, entries: list[dict], weights: dict,
               params: MilpParams) -> bytes:
    rows = sorted((e["person_id"], e["project_id"], float(e["alloc"])) for e in entries)
    return json.dumps({"dataset": dataset_version, "label": label, "entries": rows,
                       "weights": dict(sorted(weights.items())), "params": params.model_dump()},
                      sort_keys=True, ensure_ascii=False).encode("utf-8")


def sign_plan(dataset_version: str, label: str, entries: list[dict], weights: dict,
              params: MilpParams) -> str:
    return hmac.new(_SECRET, _canonical(dataset_version, label, entries, weights, params),
                    hashlib.sha256).hexdigest()


def verify_plan(token: str, dataset_version: str, label: str, entries: list[dict],
                weights: dict, params: MilpParams) -> bool:
    expected = sign_plan(dataset_version, label, entries, weights, params)
    return hmac.compare_digest(expected, token)
