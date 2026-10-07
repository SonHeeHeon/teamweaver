from pydantic import BaseModel, model_serializer


class AssignEntry(BaseModel):
    person_id: str
    project_id: str
    alloc: float
    # 월별 투입률(사용자 결정 2026-10-05). 키 = 프로젝트 진행 달(0~5). 없으면 모든 진행 달이 alloc이다.
    # 있으면 alloc은 진행 달 평균(표시·정렬·기존 지표용)이고, 제약·목적은 달별 값으로 계산한다.
    monthly_alloc: dict[int, float] | None = None

    @model_serializer(mode="wrap")
    def _omit_empty_monthly(self, handler):
        # 월별 값이 없으면 칸 자체를 내보내지 않는다 -- 기존 항목의 직렬화(저장된 교체·API 응답·서명의 항목 부분)가
        # 바이트 단위로 이전과 같다. 단, 서명은 계산 파라미터 전체(MilpParams)도 묶으므로 파라미터 칸이 늘면
        # (allocation_mode 등) 이전 토큰은 어차피 무효가 된다(리뷰 S1).
        data = handler(self)
        if isinstance(data, dict) and data.get("monthly_alloc") is None:
            data.pop("monthly_alloc", None)
        return data


class PlanAssignment(BaseModel):
    entries: list[AssignEntry]
    objective: float
    unfilled: list[str]
    # Structurally always [] for MILP solutions (core/optimize/milp.py hardcodes
    # violations=[] since the solver's own constraints -- budget, availability,
    # grade headcount -- make a constraint-violating solution infeasible by
    # construction; the only "shortfall" it can express is `unfilled` via slack).
    # Only the Greedy baseline (core/optimize/greedy.py), which has no solver to
    # reject an infeasible choice, populates this with real violation messages
    # (e.g. over-budget projects). Treat a non-empty `violations` as Greedy-only
    # signal, not something to expect from any MILP-produced plan.
    violations: list[str]
    # 솔버가 시간 한도에서 멈춘 해(최선임을 증명하기 전)인지. 화면이 "시간 한도 도달" 배지를 단다(claude-a 요청).
    time_limited: bool = False
    # 계산 신뢰도 배지(claude-a 요청 2026-10-06): 솔버 종료 사유와 HiGHS가 증명한 상한(최대화 목적의 상한).
    # 화면이 "허용 차이 안에서 최선 증명" 또는 "증명된 상한 대비 최대 X% 아래일 수 있음"을 보인다.
    termination: str | None = None
    best_bound: float | None = None
    gap_used: float | None = None       # 이 플랜을 푼 실제 허용 차이(Plan A는 min(설정, 1%), 대안은 설정값)
    label: str = "A"

    def pairs(self) -> set[tuple[str, str]]:
        return {(e.person_id, e.project_id) for e in self.entries}
