"""파라미터 해시 -> PlanAssignment 리스트 메모이제이션.

세션이 아니다: 클라이언트가 매 /api/optimize 요청에 weights/milp_params/
n_alternatives 전체를 보내는 stateless 계약은 그대로다. 이 캐시는 같은
입력이 다시 들어왔을 때 CBC를 다시 돌리지 않기 위한 순수 함수 캐시일 뿐이고,
서버가 클라이언트별 상태를 기억하는 것이 아니다."""
import hashlib
import json

from core.optimize.milp import MilpParams
from core.optimize.types import PlanAssignment


class ResultCache:
    def __init__(self):
        self._store: dict[str, list[PlanAssignment]] = {}

    @staticmethod
    def key(weights: dict, milp_params: MilpParams | dict, n_alternatives: int) -> str:
        """milp_params는 요청 원문이 아니라 *적용된* 전체 값으로 키를 만든다 --
        `{}`과 기본값을 명시한 요청은 같은 계산이므로 같은 키여야 하고, 부팅
        사전계산(저장된 설정)과 웹 요청(같은 설정을 명시)이 맞물려야 한다(K8)."""
        params = milp_params if isinstance(milp_params, MilpParams) else MilpParams(**milp_params)
        payload = json.dumps(
            {"weights": dict(sorted(weights.items())),
             "milp_params": params.model_dump(),
             "n_alternatives": n_alternatives},
            sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def get(self, key: str) -> list | None:
        return self._store.get(key)

    def put(self, key: str, plans: list) -> None:
        self._store[key] = plans
