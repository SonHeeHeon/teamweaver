"""서버가 계산한 플랜임을 보이는 서명(K10, Codex 2라운드 지적).

서버는 플랜을 저장하지 않는다(stateless). 대신 /api/optimize가 플랜마다 HMAC 토큰을
붙이고, PDF 요청은 원 플랜과 함께 그 토큰을 돌려보낸다. 토큰은 (데이터셋 버전, 라벨,
명단, 가중치, 적용 MILP 파라미터)에 묶인다 -- 하나라도 바꾸면 검증에 실패한다.

비밀키(K13에서 고정): TEAMWEAVER_PLAN_SECRET이 있으면 그것. 없으면 데이터 폴더의
plan_secret 파일(처음 한 번 무작위 32바이트로 만들고 0600)을 계속 쓴다 -- 재기동해도 같은
키라 이전 화면의 서명과 저장된 적용 교체(키가 plan_token)가 그대로 유효하다. 파일을 쓸 수
없으면 경고하고 프로세스 임시 키로 동작한다(그때는 재기동하면 서명이 무효가 된다).
"""
import hashlib
import hmac
import json
import logging
import os
import secrets

from api.storage import data_dir, load_or_create_secret
from core.optimize.milp import MilpParams

log = logging.getLogger(__name__)
_cache: dict[str, bytes] = {}


def _secret() -> bytes:
    env = os.environ.get("TEAMWEAVER_PLAN_SECRET", "").strip()
    if env:
        return env.encode()
    path = data_dir() / "plan_secret"
    key = str(path)
    if key in _cache:
        return _cache[key]
    try:
        value = load_or_create_secret(path)          # 여러 워커가 동시에 불러도 같은 키
    except (OSError, ValueError) as exc:
        log.warning("플랜 서명키를 %s에 고정하지 못해 임시 키를 쓴다(재기동하면 서명 무효): %s", path, exc)
        value = secrets.token_bytes(32)
    _cache[key] = value
    return value


def _canonical(dataset_version: str, label: str, entries: list[dict], weights: dict,
               params: MilpParams) -> bytes:
    # 월별 투입률이 있는 항목만 그 값을 서명에 넣는다 -- 없는 항목의 행은 이전과 같다(params 칸이 늘면
    # 토큰은 어차피 바뀐다).
    rows = sorted((e["person_id"], e["project_id"], float(e["alloc"]))
                  + ((tuple(sorted((int(m), float(v)) for m, v in e["monthly_alloc"].items())),)
                     if e.get("monthly_alloc") else ())
                  for e in entries)
    return json.dumps({"dataset": dataset_version, "label": label, "entries": rows,
                       "weights": dict(sorted(weights.items())), "params": params.model_dump()},
                      sort_keys=True, ensure_ascii=False).encode("utf-8")


def sign_plan(dataset_version: str, label: str, entries: list[dict], weights: dict,
              params: MilpParams) -> str:
    return hmac.new(_secret(), _canonical(dataset_version, label, entries, weights, params),
                    hashlib.sha256).hexdigest()


def verify_plan(token: str, dataset_version: str, label: str, entries: list[dict],
                weights: dict, params: MilpParams) -> bool:
    # 서명은 16진수 ASCII다. ASCII가 아닌 값은 hmac.compare_digest(str, str)가 TypeError(500)를 낸다 -- 그냥 틀린 서명이다
    # (claude-a 점검 2026-10-12 (4)). bytes로 비교한다.
    if not isinstance(token, str) or not token.isascii():
        return False
    expected = sign_plan(dataset_version, label, entries, weights, params)
    return hmac.compare_digest(expected.encode("ascii"), token.encode("ascii"))


# --- 계산 신뢰도 서명(2026-10-12, 사용자 요청 "PDF에도 표기") ---------------------------------------------
# 결과 카드의 신뢰도 배지(종료 사유·목적값·증명 상한·허용 차이)를 PDF에도 찍는다. 화면이 보낸 값을 그대로 찍으면 "최선 증명"을
# 꾸며 넣을 수 있으니, 서버가 계산할 때 원 플랜 서명(plan_token)에 묶어 따로 서명하고 PDF는 둘 다 맞을 때만 찍는다.
EVIDENCE_FIELDS = ("termination", "objective", "best_bound", "gap_allowed", "time_limited", "carried_from_k")


def _evidence_canonical(plan_token: str, evidence: dict) -> bytes:
    body = {k: evidence.get(k) for k in EVIDENCE_FIELDS}
    body["time_limited"] = bool(body["time_limited"])
    if body["carried_from_k"] is not None:
        body["carried_from_k"] = int(body["carried_from_k"])
    for k in ("objective", "best_bound", "gap_allowed"):
        if body[k] is not None:
            body[k] = float(body[k])
    return json.dumps({"kind": "solve_evidence", "plan_token": plan_token, **body},
                      sort_keys=True, ensure_ascii=False).encode("utf-8")


def evidence_of(values: dict) -> dict:
    """서명할 신뢰도 묶음(EVIDENCE_FIELDS만). 화면은 이 묶음을 그대로 돌려보낸다 -- 다시 조립하다 어긋나지 않게."""
    return {k: values.get(k) for k in EVIDENCE_FIELDS} | {"time_limited": bool(values.get("time_limited"))}


def sign_evidence(plan_token: str, evidence: dict) -> str:
    return hmac.new(_secret(), _evidence_canonical(plan_token, evidence), hashlib.sha256).hexdigest()


def verify_evidence(token: str, plan_token: str, evidence: dict) -> bool:
    if not isinstance(token, str) or not token.isascii() or not isinstance(plan_token, str):
        return False
    expected = sign_evidence(plan_token, evidence)
    return hmac.compare_digest(expected.encode("ascii"), token.encode("ascii"))
