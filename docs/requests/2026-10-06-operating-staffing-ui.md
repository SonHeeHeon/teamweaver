# [claude-a → claude-b] 운영 중 편성·사업 보강 화면 + API (2026-10-06)

사용자 요청(원문 요약): "대부분 이미 배치된 상황에서 이 시스템을 쓴다. 신규 제안 2~3개를 최근 끝난 10명 안팎으로 짠다.
① 기존 사업에서 1~2명을 빼 신규에 넣고 남는 인력을 그 자리에 넣었을 때 전체가 얼마나 좋아지는지 알려주는 기능
② 진행 중 사업이 어려울 때 누구를 넣을지 넣었다 뺐다 하며 시뮬레이션하는 기능". 계획: `.omc/plan/2026-10-06-operating-staffing.md`(claude-a 로컬).

계산(core)은 claude-a가 만들었다(`feat/claude-a-operating-staffing`). API·화면을 부탁한다. 계약은 아래 함수 그대로다.

## 데이터
- 새 시나리오 묶음: `generate_org_bundle(..., scenario="operating")` → `demo/org-n100-operating`(claude-a가 `rehearsal.make_demo`로 만들 예정).
  manifest에 `scenario: "operating"`, `bench: [person_id…]`, `proposals: [project_id…]`.
- 현재 배치 = `Dataset.current`(`current_assignments.csv`): 진행 중 사업 명단·투입률·잠금. 기준 배치(entries)는 이것을
  `AssignEntry(person_id, project_id, alloc)`로 바꾼 것(교체·조정을 적용했다면 적용된 배치).

## API 제안(이름은 claude-b가 정해도 된다)
1. `POST /api/operating/compare` — S1 신규 제안 편성 + 변경 예산
   - 입력: `{dataset_version, milp_params, ks: [0,1,2,3]}`
   - 계산: `core.evaluate.operating.compare_move_budgets(graph, S, C, params, dataset.current, ks)` (K마다 MILP 1회, 최적까지.
     한 K가 실패해도 계속하고, 시간 한도로 K가 커졌는데 나빠지면 직전 해를 유지해 `carried_from_k`로 표시, `proven_optimal` 칸 —
     실측 100명 K=0..3: 0.5 / 1.2 / 11 / 28초, 200·300명은 `rehearsal/results/operating-check.json`). K별로 SSE로 흘리면 좋다.
   - 출력 행(K마다): `k, elapsed_s, termination, quality(기술+협업−익숙함), unfilled_seats, objective, parts,
     quality_gain_vs_k0, quality_gain_pct_vs_k0, unfilled_change_vs_k0, diff{kept, moved[{person_id, from, to[]}],
     joined[{person_id, project_id, from_bench}]}, project_change_vs_k0{project_id: Δ}, violations, entries`
   - 화면: K별 비교표(빈자리·배치 품질·시간), K 선택 시 이동 그림("DP0013: J005 → J014·J015, 그 자리에 DP0081"),
     사업별 변화. 문구: 빈자리 감점이 총점을 좌우하므로 **배치 품질과 빈자리를 나눠** 보여 준다.
2. `POST /api/staffing/candidates` — S2 보강 후보 순위
   - 입력: `{dataset_version, milp_params, entries, project_id, include_pull, budget_add|null, top}`
   - 계산: `core.evaluate.staffing_sim.rank_candidates(..., locked=잠긴 (사람, 사업) 집합)` — 빠른 근사로 40명 거른 뒤 정확 채점
     (100명 약 2초, 300명 약 9초). budget_add=null이면 후보 비용만큼 예산을 늘려 본다(필요 예산 = monthly_cost), 숫자면 그 안으로
     투입률을 맞춘다. 잠긴 배치는 빼 오지 않는다.
   - 출력 행: `person_id, grade, alloc, source(bench|partly_free|pull), pulled_from[], delta_total, delta{skill,synergy,
     overfamiliarity,unfilled}, skill_fit, team_synergy, project_total_after, monthly_cost, budget_added, new_violations`
   - 화면: 사업 선택 → 후보표(이유: 기술 적합·팀 협업·익숙한 쌍·비용·빼 오면 생기는 빈자리), "넣어 보기" 버튼.
3. `POST /api/staffing/simulate` — 넣기·빼기 재평가
   - 입력: `{dataset_version, milp_params, entries, adds:[{person_id, project_id, alloc}], removes:[[person_id, project_id]],
     extra_seats:{등급: n}, budget_add}` → 그래프는 `graph_with_extra_seats(graph, project_id, extra_seats, budget_add)`를
     graph_after로 넘긴다(보강 전은 원래 그래프).
   - 계산: `staffing_sim.simulate(graph, S, C, params, entries, adds, removes, graph_after)` → `before, after, delta,
     violations, new_violations, entries`. AI 설명은 기존 교체 설명을 재사용(넣은 사람 기준).
4. `POST /api/staffing/best` — 최선 n명
   - 입력: `{dataset_version, milp_params, entries, project_id, n, grade|null, pull_budget, budget_add|null}`
   - 계산: `staffing_sim.best_additions(..., locked=…)`(100명 1초 안팎) → `accepted, termination, added_cost, diff, before, after,
     delta, violations, entries`. budget_add=null이면 예산을 제약으로 쓰지 않고 실제 든 비용(added_cost)을 보여 준다.

## 주의
- `milp_params`는 지금처럼 설정 화면 값을 그대로 받는다(시드 수는 서버 환경 변수 `TEAMWEAVER_SOLVER_SEEDS`).
- 이 계산들은 갭 0으로 푼다(`operating.exact`) — 빈자리 감점이 섞인 목적에서 5% 갭이 개선을 가렸다.
- 사업 효과는 NOT_CALIBRATED — 화면 문구는 "계산상 개선(같은 평가 기준)"으로.
- 시연 확장 A(단순 규칙 대비): `core.evaluate.baseline.compare_with_baseline(graph, S, C, params, entries)` → 결과 화면에
  "단순 규칙(기술 1등 우선) 대비" 카드. 결과: `rule, optimized{…}, baseline{…}, difference{…}`. 고정 데이터 100명 실측:
  총점 +12.1(기술 +7.6, 협업 +4.3), 단순 규칙은 예산 위반 1건. 자리당 평균 적합은 최적화가 낮다(정원에 없는 등급을 예산
  안에서 더 넣어 총 기여를 늘리는 모델 특성) — 숨기지 말고 함께 표시.
- 시연 확장 B(계산 신뢰도): 이미 요청한 "상한 대비 최대 X%·독립 검증 통과·시간 한도" 배지(`docs/work-split.md`).

## 추가(2026-10-07): 시연 데이터 고르기 통일 + 미리 계산 결과 — 서버는 claude-a(`feat/claude-a-demo-presets`)
사용자 결정: "시연 기본 데이터로 둘 다 하자(연초 계획 = 인력 전체, 운영 중 = 종료 인력 몇 명을 신규 프로젝트에)", 화면은 claude-b.
고르기가 두 벌 구현돼 있어 **claude-b 계약(`api/demos.py`, `{name}`)으로 통일**했다(사용자 결정 2026-10-07). claude-a 쪽 목록·전환은 지웠다.

**서버 계약(웹은 그대로 동작 — 칸만 추가)**
- `GET /api/datasets/demos` → 배열. 기존 칸 `name, dataset_id, people, projects, scenario, synthetic` 그대로 +
  `title`("연초 계획 · 100명 전체 배치" / "운영 중 · 90명 배치 중, 대기 10명"), `description`(한 줄), `current`, `bench`, `proposals`.
  **zip 묶음도 나온다**(200·300명: `org-n200`, `org-n200-operating`, `org-n300`, `org-n300-operating`). `-broken`(업로드 검증 시연용)은 빠진다.
  순서: 인원 → 연초 계획 → 운영 중.
- `POST /api/datasets/demo {name}` 그대로. 응답에 `demo_name`, `precomputed` 추가.
- `/api/datasets/active`에 `demo_name`(지금 시연 묶음 이름 또는 null)과 `precomputed`(`{preset, computed_at, plans, operating, skipped[]}` 또는 null).
- `/api/optimize`: 요청 `fresh: true`면 캐시를 무시하고 다시 푼다. plan·done 프레임에 `precomputed_at`(미리 계산 결과면 계산 시각, 아니면 null).
- `/api/operating/compare`: 요청 `fresh`(기본 false). 미리 계산 행이 맞으면 계산 없이 start·row·done에 `precomputed_at`을 싣고 흘린다
  (claude-a가 네 라우트에 분기를 넣었다). `compare_move_budgets(on_row=)`도 넣었다 — K별 실시간 송출은 네가 바꾸기로 한 대로.

**화면 제안**
1. 데이터 탭 시연 데이터 고르기에 `title`·`description`을 쓰면 장면이 바로 읽힌다(200·300명 zip 묶음도 목록에 나온다).
2. 결과 카드·운영 중 비교: `precomputed_at`이 있으면 "미리 계산한 결과 · 시각(같은 데이터·같은 설정)" 배지 + "다시 계산"(`fresh: true`).
   `time_limited` 배지는 그대로 함께 보인다. **미리 계산을 실시간 계산처럼 보이게 하지 않는다**(시연 정직성).
3. 데이터 탭(관리자): `precomputed.skipped`가 있으면 "미리 계산 결과를 쓰지 않음: <이유>"(시연 전 점검용).
4. 운영 중 묶음에서 "계산 시작"(전원 다시 짜기)을 누르면 200·300명은 시간 한도 안에 빈자리가 남는다 — 안내 문구를 붙이면 좋다.
