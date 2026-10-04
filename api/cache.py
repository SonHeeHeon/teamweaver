"""파라미터 해시 -> PlanAssignment 리스트 메모이제이션.

세션이 아니다: 클라이언트가 매 /api/optimize 요청에 weights/milp_params/
n_alternatives 전체를 보내는 stateless 계약은 그대로다. 이 캐시는 같은
입력이 다시 들어왔을 때 CBC를 다시 돌리지 않기 위한 순수 함수 캐시일 뿐이고,
서버가 클라이언트별 상태를 기억하는 것이 아니다."""
import hashlib
import json
from collections import OrderedDict

from core.optimize.milp import MilpParams
from core.optimize.types import PlanAssignment


class ResultCache:
    """최근 MAX_ENTRIES개만 기억하는 LRU. 데이터셋을 바꿀 때마다 옛 버전 키가 쌓여 메모리가
    계속 늘던 것을 막는다(claude-a 교차 리뷰 L2). 부팅 사전계산 결과도 다른 키처럼 밀려날 수 있다."""
    MAX_ENTRIES = 32

    def __init__(self):
        self._store: OrderedDict[str, list[PlanAssignment]] = OrderedDict()

    @staticmethod
    def key(weights: dict, milp_params: MilpParams | dict, n_alternatives: int,
            dataset_version: str) -> str:
        """milp_params는 요청 원문이 아니라 *적용된* 전체 값으로 키를 만든다 --
        `{}`과 기본값을 명시한 요청은 같은 계산이므로 같은 키여야 하고, 부팅
        사전계산(저장된 설정)과 웹 요청(같은 설정을 명시)이 맞물려야 한다(K8).
        dataset_version은 활성 데이터셋 내용 해시다 -- 업로드로 데이터가 바뀌면
        같은 요청이라도 이전 데이터의 결과를 돌려주지 않는다(K9)."""
        params = milp_params if isinstance(milp_params, MilpParams) else MilpParams(**milp_params)
        payload = json.dumps(
            {"weights": dict(sorted(weights.items())),
             "milp_params": params.model_dump(),
             "n_alternatives": n_alternatives,
             "dataset": dataset_version},
            sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def get(self, key: str) -> list | None:
        plans = self._store.get(key)
        if plans is not None:
            self._store.move_to_end(key)
        return plans

    def put(self, key: str, plans: list) -> None:
        self._store[key] = plans
        self._store.move_to_end(key)
        while len(self._store) > self.MAX_ENTRIES:
            self._store.popitem(last=False)
