# TeamWeaver — SI 조직 인력 배치 최적화 시제품

> **범위**: PoC이자 과제 제출용 시제품이다. 실제 인사 자료는 적재하지 않으며(불가), 모든 데이터는 가상(seed 고정)이고
> 사업 효과는 검증하지 않았다(`NOT_CALIBRATED`). 실데이터를 전제로 한 작업은 "실제 도입 시 필요한 것"으로만 기록한다.

직원과 사업을 **기술 적합(S)·협업 이력(C)·월별 가용률·월 예산·등급 정원**을 한꺼번에 맞춰 배치하는 도구다.
배치 결정은 수학 최적화(MILP, HiGHS)가 하고, 솔버가 낸 답은 원자료로 **다시 검사(독립 검증)**한 뒤에만 화면에 낸다.
AI(LLM)는 배치를 정하지 않고 글만 쓴다 — 동료 평가 글 판정, 교체 설명, 인사팀 소명 글. AI 글은 모두 기계 검사를 통과해야 나가고,
실패하면 정해진 틀의 글로 대신한다.

| 기능 | 하는 일 |
|---|---|
| 연초 계획 | 백지에서 6개월 배치 안 A~D(구성이 다르고 품질이 A의 95% 이상), 계산 신뢰도 배지(최선 증명·최대 몇 % 차이), 계산 멈추기 |
| 운영 중 편성 | 이미 배치된 조직에서 최대 K명(0~3)만 옮겨 신규 제안·빈자리를 채울 때 K별 개선, 진행 사업 보강(후보 순위·최선 n명) |
| 교체 검토(What-if) | "A 대신 B"를 같은 목적·제약으로 다시 채점(참고값) + 근거를 원문과 대조한 AI 설명, 새 위반이 생기면 결론은 "보류" |
| 인사팀 소명 글 | 지식 그래프 사실로 "왜 이 팀인가"를 씀(GraphRAG, 숫자는 서버가 채움), AI가 빠뜨린 불리한 사실은 서버가 덧붙임 |
| 조직 기술 지도 | 기술별 실무 가능·숙련·비어 있는 사람·요구 사업·공급/수요 |
| 데이터·PDF | 실제 인사 자료 모양의 CSV 묶음 업로드(오류를 파일·줄·칸까지), 서버가 서명을 확인하고 다시 계산해 찍는 PDF |

## 제출물 — 먼저 볼 것

| 문서 | 내용 |
|---|---|
| [`outputs/final-report.html`](outputs/final-report.html) | **최종 보고서** — 무엇을 만들었고, 계산이 맞는지, 무엇이 한계인지 |
| [`docs/demo-script.md`](docs/demo-script.md) | **시연 시나리오·대본** — 장면별 누를 것·보이는 것·말할 것, 짧은 판, 막혔을 때 |
| [`docs/architecture.html`](docs/architecture.html) | 한 장 구조도 |
| [`outputs/eli5-project-history-roadmap.html`](outputs/eli5-project-history-roadmap.html) | **프로젝트 전체 해설(최종판)** — 비전문가용, 배경·개발 이력·실험·신뢰 범위 |
| [`docs/demo-notes.md`](docs/demo-notes.md) | 설계 판단 설명 노트(협업 쌍 상한 200, 익숙한 쌍 기준, 문헌 근거, "그냥 GPT에게 시키면?") |
| [`docs/skill-score-decision.md`](docs/skill-score-decision.md) · [`docs/kg-technology-decision.md`](docs/kg-technology-decision.md) · [`docs/model-roadmap.md`](docs/model-roadmap.md) | 결정 기록 |

HTML 문서는 외부 리소스 없이 열리는 단일 파일이다 — 저장소를 받아 브라우저로 연다(GitHub 화면에서는 소스로 보인다).

## 실행 (시연)

필요한 것: Python 3.12와 [uv](https://docs.astral.sh/uv/), Node.js(npm). macOS에서 개발·리허설했다.

```bash
scripts/run_poc.sh                 # 의존성·웹 빌드·PDF용 Chromium 설치 후 http://127.0.0.1:8000
PORT=8010 scripts/run_poc.sh       # 8000번이 쓰이고 있을 때
SKIP_SETUP=1 scripts/run_poc.sh    # 준비 단계를 이미 한 번 했을 때
```

- 첫 화면은 시연 묶음 `demo/org-n100`(가상 100명·사업 20건)이다. 데이터 탭의 "시연 데이터 고르기"로 100·200·300명 × 연초 계획/운영 중
  6개 묶음을 고른다. `demo/precomputed/`에 같은 데이터·같은 설정으로 미리 계산한 결과가 있어 바로 뜬다(화면에 "미리 계산"으로 표시).
- 저장소 루트의 `.env`에 `OPENAI_API_KEY`가 있으면 교체 설명·소명 글을 AI가 쓴다. 없으면 정해진 틀(규칙 기반)의 글로 나온다.
- 다른 사람이 접속하는 주소(`HOST=0.0.0.0`)로 띄울 때는 관리자 비밀번호를 정한다(`scripts/run_poc.sh` 머리말).
- 업로드한 묶음·설정·서명 키는 `TEAMWEAVER_DATA_DIR`(기본 `~/.teamweaver`)에 남는다.

## 개발·검증

| 할 일 | 명령 | 기준(2026-10-12) |
|---|---|---|
| 설치 | `uv sync` (SCIP·벤치 포함은 `uv sync --group benchmark`) | |
| 시험 | `uv run --group benchmark pytest -q` | 1806 passed, 1 skipped, 19 deselected |
| 느린 전 과정 시험 | `uv run pytest -m slow` | 19 passed |
| 정답 대조(Phase 0) | `uv run --group benchmark python -m experiments.bench.phase0_model` | PASS 11건 |
| 웹 시험·타입체크 | `cd web && npm test` · `npx tsc -b` | 202 passed (`tsc --noEmit`은 프로젝트 참조 구조라 아무것도 검사하지 않는다) |
| API 개발 서버 | `TEAMWEAVER_SKIP_WARM=1 uv run uvicorn api.main:app --reload` | 웹 개발 서버는 `cd web && npm run dev`(`:5173` → API `:8000`) |
| PDF 준비 | `cd web && npm run build` · `uv run playwright install chromium` | PDF는 빌드된 화면의 `/report`를 인쇄한다 |

## 저장소 구조

| 경로 | 내용 |
|---|---|
| `core/domain` | 입력 계약(Pydantic) — 6개월 계획, 레벨 1~5, 4등급 |
| `core/ingest` | CSV 묶음 → 데이터셋(계약·검증 리포트·가상 묶음 생성), 기술 이름 사전 |
| `core/graph`, `core/scoring` | 관계 그래프·근거 색인(SQLite), 기술 적합 S·협업 C |
| `core/optimize` | MILP(PuLP+HiGHS), 시드 포트폴리오, 대안, 독립 검증, 운영 중 K 제약, 계산 멈춤 |
| `core/evaluate` | 평가기(어떤 배치든 같은 목적·제약으로 채점), 운영 중 비교, 보강 시뮬레이터, 단순 규칙 대비, 과거 성과 점검 |
| `core/kg` | 지식 그래프(소명 글 사실·조직 기술 지도) |
| `api/` | FastAPI(SSE 최적화, What-if, 운영 중, 데이터셋, 관리자 설정, PDF). `api/rag/`는 교체 설명·소명 글의 AI 호출과 검사 |
| `web/` | React + TypeScript + Tailwind 화면 |
| `demo/` | 시연 묶음 6개 + 일부러 틀린 묶음, 미리 계산 결과, 평가 글 판정값 |
| `rehearsal/` | 규모 리허설·측정 스크립트, `rehearsal/results/`에 측정 보고서 |
| `experiments/` | Phase 0(정답 대조)·Phase 1(솔버 비교)·초기 실험 |
| `docs/`, `outputs/` | 설계·결정·시연 문서, 보고서·해설 |

## 개발 방식

Claude 두 계정(claude-a·claude-b)이 파일 영역을 나눠 개발하고 Codex가 리뷰했다. 영역·요청은 `docs/work-split.md`,
날짜별 기록은 `docs/handoff-log.md`, AI 도구용 안내는 `CLAUDE.md`·`AGENTS.md`에 있다. 압축 맥락은 `docs/project-context.md`.

## 부록: 초기 검증 기록(영문, 2026-08~10)

아래는 초기 개발 때 쓴 기술 기록이다. 숫자는 그 시점의 값이다.

### Phase 0 model validation

Phase 0 and Phase 1 were merged into `main` on 2026-10-03.
The [24-hour open-source solver sweep design](docs/superpowers/specs/2026-09-21-phase1-long-sweep-design.md)
and [visual overview](outputs/phase1-long-sweep-design.html) define the frozen
experiment. The `phase1-long-sweep-v2` schedule is now **COMPLETE (357/357)**;
see the [evidence report](outputs/phase1-solver-benchmark.html),
[Korean ELI5 handoff](outputs/eli5-phase1-long-sweep-v2.html), and
[development checkpoint](docs/phase1-checkpoint.md). The design supersedes the
earlier decision to defer all
synthetic large-scale comparison until HR column definitions arrive; business
validity remains `NOT_CALIBRATED`.

Run the bounded mathematical validation before comparing additional solvers:

```bash
uv run python -m experiments.bench.phase0_model
uv run python -m experiments.phase0.report \
  experiments/results/phase0_model_validation.json \
  outputs/phase0-model-validation.html
```

The runner has a 240-second wall-clock limit: each CBC and tiny-oracle linear
program receives only the remaining time (with a small shutdown margin), and a
deadline stops later checks while recording the failure. It writes an atomic checkpoint after every case to
`experiments/results/phase0_model_validation.json`; a failed replacement keeps
the prior canonical checkpoint readable. It checks the current MILP formula
against an independent tiny-problem oracle (including partial pair pruning),
rederives constraints and pair scopes outside the solver, records monotonicity
and pair-pruning observations, and runs a 50-person × 10-project CBC smoke
test.

`PASS` means the current mathematical implementation passed these synthetic
calculation checks. It does **not** mean TeamWeaver has demonstrated improved
customer satisfaction or project outcomes; that remains `NOT_CALIBRATED`
until independently recorded real outcomes are available.

### Phase 1 open-source solver benchmark

Phase 1 compares CBC, HiGHS, and SCIP on one frozen MILP schedule. Every solver
runs single-threaded, serially, in a fresh process. Results, time use, versions,
options, input hashes, validation evidence, and unavailable backends are stored
under `experiments/results/phase1/<run-id>/`. This is still a synthetic technical
comparison, so business validity remains `NOT_CALIBRATED`.

The completed v2 run recorded `DONE 197`, `NO_VALID_INCUMBENT 87`,
`DEADLINE_EXCEEDED 68`, and five conservatively discarded interrupted cases.
HiGHS became the service solver on 2026-10-05. At the time of this benchmark the 300-person /
60-project cases were not production-ready (fixed later by capping reward pairs at 200 -- see `docs/demo-notes.md`): only 6/37 HiGHS, 3/37 CBC, and 0/37
SCIP cases completed within the recorded limits.

Run the three-case start gate first (maximum active budget: 720 seconds):

```bash
TIKTOKEN_CACHE_DIR=/private/tmp/teamweaver-tiktoken-cache uv run --group benchmark \
  python -m experiments.phase1.runner \
  --run-id phase1-start-gate --max-active-seconds 720 --max-cases 3
TIKTOKEN_CACHE_DIR=/private/tmp/teamweaver-tiktoken-cache uv run --group benchmark \
  python -m experiments.phase1.report \
  experiments/results/phase1/phase1-start-gate \
  outputs/phase1-solver-benchmark.html
```

The first three cases compare all solvers on one tiny oracle. Before a long
run, continue the same gate through the remaining tiny oracles and one frozen
50-person/10-project compatibility case per solver (24 recorded cases total):

```bash
TIKTOKEN_CACHE_DIR=/private/tmp/teamweaver-tiktoken-cache uv run --group benchmark \
  python -m experiments.phase1.runner \
  --run-id phase1-start-gate --max-active-seconds 720 --max-cases 21 --resume
```

Do not start the long run if the gate records an unavailable solver, failed
oracle parity, invalid solution, missing evidence, or a paused checkpoint. Only
after the generated report confirms the gate may the pre-registered long sweep
be started or resumed with the same run ID:

```bash
TIKTOKEN_CACHE_DIR=/private/tmp/teamweaver-tiktoken-cache uv run --group benchmark \
  python -m experiments.phase1.runner \
  --run-id phase1-long-sweep-v2 --max-active-seconds 86400 --resume
```

The same `--resume` command is safe for the first invocation and later
continuations. A stored manifest or source/dependency mismatch fails closed;
it is never silently replaced.

### Demo fixture (`fixtures/*.json`)

`fixtures/{people,projects,coworks,reviews_ko,parsed_reviews,meta}.json` are the
frozen fixture dataset (100 people / 20 projects / seed 42) that the early experiments and
the E2E smoke test (`tests/test_e2e_smoke.py`) consume. The demo itself now runs on the
CSV bundles in `demo/` (see the top of this README). Generate/refresh them with `scripts/generate_fixtures.py`:

```bash
uv run python scripts/generate_fixtures.py --people 100 --projects 20 --seed 42 --review-mode llm
```

**The fixture committed to this repo is LLM mode** (`review_mode: "llm"` in
`meta.json`, refrozen 2026-08-07):

- `reviews_ko.json` contains natural, independently-written Korean peer
  reviews generated by `gpt-5-mini` (`core/datagen/llm_reviews.py`'s prompt,
  via `rewrite_reviews_with_llm`).
- `parsed_reviews.json` was produced by the LLM structured-output parser
  running on `gpt-5-nano` (`parse_reviews_llm`).
- Both models are pinned in `fixtures/pricing.json` (`gen_model`/`parse_model`,
  `as_of: 2026-08-05`).

**Actual cost of the 2026-08-07 refreeze**: 266 reviews × (1 generation call +
1 parse call) = 532 API calls, **$0.7021 total** — `gpt-5-mini` generation:
40,922 prompt + 258,137 completion tokens ($0.5265); `gpt-5-nano` parsing:
74,390 prompt + 429,783 completion tokens ($0.1756). (The full token/cost breakdown is in a local work log, `.omc/`, which is not part of the repository.)

**LLM regeneration is resumable / checkpointed.** A 266-review run is 500+
sequential API calls at reasoning-model latency (~10–25s/call observed) —
well over an hour end to end — and this environment has been observed to
kill long-running background processes outright (the same failure mode that
hit the earlier CBC benchmark sweeps). `scripts/generate_fixtures.py
--review-mode llm` therefore checkpoints every review's generate+parse result
to `.omc/llm_checkpoint/seed{seed}_p{people}_pr{projects}.json`
(`core/datagen/llm_checkpoint.py`) immediately after that review completes,
and skips reviews already present in the checkpoint on the next invocation —
a kill loses at most one in-flight review, and a review is never billed
twice. Use `--llm-batch-size N` to bound how many *new* reviews a single
invocation processes (the 2026-08-07 refreeze used `--llm-batch-size 13`,
~5–8 min/invocation, run ~20 times); re-run the identical command until it
prints `frozen: ...` instead of `NOT DONE: ... remaining`.

### `text_polarity` is now independent of `item_score`

Previously (template mode), the free-text sentiment carried no information
beyond the structured checkbox items: `text_polarity` was a deterministic
function of item counts (`(n_pos-n_neg)/(n_pos+n_neg)`), because
`_template_text` (`core/datagen/generator.py`) built the narrative text
*from* the selected items with a fixed closing clause per polarity. (An
earlier attempt to fix this with a small Korean sentiment-cue lexicon made it
worse — constant `text_polarity` across the corpus, which halved
`pair_review_score`'s standard deviation, 0.325 → 0.163 — and was reverted.)

**On the 2026-08-07 LLM-mode fixture this limitation no longer holds**:
measured `max |text_polarity − item_score| = 0.9167` (mean 0.281) across all
266 reviews — the LLM parser reads free-text sentiment independently of the
checkbox selections. (Example: one review with 5 positive / 1 negative items,
`item_score = 0.667`, parsed at `text_polarity = 0.0` because the prose read
as more balanced than the checkbox tally.) The β term of the synergy matrix
`C` (`core/scoring/engine.py::synergy_matrix`, `0.5*item_score +
0.5*text_polarity` inside `MemoryGraph.build`) now has a real, independent
free-text arm — the "Hybrid Data Pipeline" architecture element and
**Experiment 2's premise (that LLM-parsed free text adds information
structured checkboxes don't) are both substantiated on this fixture.**
`tests/test_parse_reviews.py::test_llm_mode_text_polarity_is_independent_of_item_score`
pins this as a regression guard (asserts `meta["review_mode"] == "llm"` and
`max |text_polarity − item_score| > 0.05`).

**No datagen tuning was applied to hit any headline number** (spec §4.4
forbids it) — not for the original template freeze, and not for this LLM
refreeze. `core/datagen/generator.py` is unchanged from Task 3 —
people/project skill difficulty is realistic (not eased to make the
optimizer's job trivial). Why: the spec's headline metric is **최적화율 (optimization ratio)** —
`Σ S_ij·a_ij` of the solved plan divided by the LP-relaxation upper bound of
that same skill objective (`core/optimize/metrics.py::optimization_ratio`) —
not raw matching fulfillment. New LLM-generated review text changes the C
matrix, so the ratio moved slightly on refreeze: **0.9278 → 0.9300**
(matching_fulfillment 0.5436 → 0.5476, unfilled=0 both before and after) —
still comfortably above the ≥0.90 gate, recorded as-is with no tuning. Plan A
reaches optimization_ratio ≈ 0.93 with zero unfilled required slots, while
matching fulfillment (a stricter, capacity-consuming secondary metric — the
same person's allocation is split across every skill slot they qualify for)
reads ≈ 0.55 by construction, not because staffing is short.  `tests/test_e2e_smoke.py`
also solves Greedy on the same frozen fixture and asserts MILP's
optimization_ratio beats it (MILP ≈ 0.93 vs Greedy ≈ 0.71) as a regression
guard on the metric itself.
