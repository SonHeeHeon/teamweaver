import { useEffect, useMemo, useRef, useState } from "react";
import {
  DatasetChangedError, fetchOperatingState, postStaffingBest, postStaffingCandidates,
  postStaffingSimulate, streamOperatingCompare,
} from "../api/client";
import type {
  BestResult, Candidate, Meta, OperatingDiff, OperatingRow, OperatingState, Parts, PlacementSettings,
  SimulateResult,
} from "../api/types";
import { ConfidenceBadge } from "./ConfidenceBadge";
import { PrecomputedNotice } from "./PrecomputedNotice";

/** 운영 중 편성(신규 제안 + 변경 예산 K)·진행 사업 보강 화면(claude-a 요청 2026-10-06, 계산 core.evaluate).
 *  총점에는 빈자리 감점(자리당 큰 값)이 섞여 있어 **배치 품질(기술+협업−익숙함)과 빈자리를 나눠** 보인다.
 *  문구는 "계산상 개선(같은 평가 기준)" -- 사업 효과는 NOT_CALIBRATED. */
interface Props {
  meta: Meta;
  params: PlacementSettings | null;
  onDatasetChanged: () => void;
}

const SOURCE_LABEL: Record<string, string> = { bench: "대기 인력", partly_free: "일부 여유", pull: "다른 사업에서 빼 옴" };
const PART_LABEL: Record<string, string> = {
  total: "총점", skill: "기술 적합", synergy: "협업 보상", overfamiliarity: "익숙한 쌍 감점", unfilled: "빈자리 감점",
};

function signed(x: number | null | undefined, digits = 2): string {
  if (x == null) return "–";
  return `${x > 0 ? "+" : ""}${x.toFixed(digits)}`;
}

export function OperatingTab({ meta, params, onDatasetChanged }: Props) {
  const [state, setState] = useState<OperatingState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const person = useMemo(() => new Map(meta.people.map((p) => [p.id, p])), [meta.people]);
  const project = useMemo(() => new Map(meta.projects.map((p) => [p.id, p])), [meta.projects]);
  const who = (id: string) => (person.get(id) ? `${person.get(id)!.name}(${id})` : id);
  const proj = (id: string) => (project.get(id) ? `${project.get(id)!.name}(${id})` : id);
  const common = { dataset_version: meta.dataset_version, milp_params: params };

  // App이 렌더마다 새 함수를 넘기므로 ref로 최신 것만 쓴다(상태 다시 읽기는 데이터셋이 바뀔 때만)
  const onChanged = useRef(onDatasetChanged);
  useEffect(() => { onChanged.current = onDatasetChanged; });

  const [reload, setReload] = useState(0);
  useEffect(() => {
    let alive = true;
    setError(null);                     // 이전 데이터의 오류를 새 데이터 화면에 남기지 않는다
    // 옛 데이터의 상태도 비운다 -- 새 상태 조회가 실패하면 오류가 보여야지 '불러오는 중…'에 멈추면 안 된다(Codex 사후 리뷰 S)
    setState(null);
    fetchOperatingState().then((s) => {
      if (!alive) return;
      setState(s);
      // meta를 읽은 뒤 서버 데이터가 또 바뀌었다: 로딩에서 멈추지 않게 화면 전체를 새 데이터로 다시 불러온다(리뷰 2라운드)
      // (계산할 수 있는 운영 데이터일 때만 -- 안내만 보이는 데이터는 옛 버전이어도 계산이 없다)
      if (s.available && s.dataset_version !== meta.dataset_version) onChanged.current();
    }).catch((e) => alive && setError(String(e)));
    return () => { alive = false; };
  }, [meta.dataset_version, reload]);

  function fail(e: unknown) {
    if (e instanceof DatasetChangedError) onDatasetChanged();
    else setError(e === null ? null : String(e));
  }

  if (error && !state) {
    return (
      <p role="alert" className="text-sm text-red-700">
        {error}{" "}
        <button onClick={() => setReload((n) => n + 1)} className="ml-2 text-xs underline">다시 불러오기</button>
      </p>
    );
  }
  // 데이터셋이 바뀌었는데 상태를 아직 다시 못 읽었으면 옛 상태로 계산하지 않는다(리뷰 S4)
  if (!state || (state.available && state.dataset_version !== meta.dataset_version)) {
    return <p className="text-sm text-slate-500">불러오는 중…</p>;
  }
  if (!state.available) {
    return (
      <section className="max-w-3xl rounded-lg border border-slate-200 bg-white p-4 text-sm text-slate-700">
        <h2 className="mb-1 text-lg font-semibold text-slate-900">운영 중 편성</h2>
        <p>{state.hint}</p>
      </section>
    );
  }
  return (
    <div className="space-y-8">
      <p className="text-xs text-slate-500">
        {state.note} · 기술 적합은 요건 가중치 없이(모든 기술 같은 무게) 계산한다.{state.hint ? ` ${state.hint}` : ""}
      </p>
      {error && <p role="alert" className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>}
      {/* key: 데이터셋이 바뀌면 하위 상태(비교 결과·후보·넣기/빼기 묶음)를 모두 버린다(리뷰 S4) */}
      <MoveBudget key={`m-${state.dataset_version}`} state={state} common={common} who={who} proj={proj} fail={fail} />
      <Reinforce key={`r-${state.dataset_version}`} state={state} common={common} who={who} proj={proj} fail={fail} />
    </div>
  );
}

type Common = { dataset_version: string; milp_params: PlacementSettings | null };
type Helpers = { state: OperatingState; common: Common; who: (id: string) => string; proj: (id: string) => string;
                 fail: (e: unknown) => void };

// --- S1: 신규 제안 편성(변경 예산 K) ------------------------------------------------------------

function MoveBudget({ state, common, who, proj, fail }: Helpers) {
  const [ks, setKs] = useState<number[]>([0, 1, 2, 3]);
  const [rows, setRows] = useState<OperatingRow[]>([]);
  const [running, setRunning] = useState(false);
  const [elapsed, setElapsed] = useState<number | null>(null);
  const [pick, setPick] = useState<number | null>(null);
  // 미리 계산 결과(시연 묶음)면 그 계산 시각 -- 시간 칸이 방금 푼 시간처럼 보이지 않게 밝힌다(claude-a 리허설 요청)
  const [preAt, setPreAt] = useState<string | null>(null);
  const expected = ks.reduce((a, k) => a + (state.expected_s_100[String(k)] ?? 0), 0);
  const expectedS = Math.ceil(expected * Math.max(1, state.n_people / 100));

  /** fresh: 미리 계산 행을 쓰지 않고 다시 푼다. 버튼 onClick이 이벤트를 넘겨도 켜지지 않게 true만 본다. */
  async function run(fresh?: unknown) {
    setRunning(true);
    setRows([]);
    setPick(null);
    setPreAt(null);
    setElapsed(0);
    fail(null);
    try {
      for await (const ev of streamOperatingCompare({ ...common, ks, ...(fresh === true ? { fresh: true } : {}) })) {
        if (ev.event === "start") setPreAt(ev.data.precomputed_at ?? null);
        else if (ev.event === "progress") setElapsed(ev.data.elapsed_s);
        else if (ev.event === "row") setRows((r) => [...r, ev.data]);
        else if (ev.event === "done") setElapsed(ev.data.elapsed_s);
        else if (ev.event === "error") throw new Error(ev.data.message);
      }
    } catch (e) { fail(e); }
    finally { setRunning(false); }
  }

  const chosen = rows.find((r) => r.k === pick) ?? null;
  return (
    <section className="space-y-3">
      <div>
        <h2 className="text-lg font-semibold text-slate-900">신규 제안 편성 — 기존 사업에서 몇 명까지 옮길까(변경 예산 K)</h2>
        <p className="text-sm text-slate-500">
          신규 제안 {state.proposals.map(proj).join(", ") || "없음"}을 대기 인력 {state.bench.length}명으로 짠다.
          K명까지 기존 사업에서 옮기고 그 빈자리를 남는 인력으로 메웠을 때 전체가 얼마나 좋아지는지 K별로 비교한다.
        </p>
      </div>
      <div className="flex flex-wrap items-center gap-3 text-sm">
        {[0, 1, 2, 3].map((k) => (
          <label key={k} className="flex items-center gap-1">
            <input type="checkbox" checked={ks.includes(k)} disabled={running}
                   onChange={(e) => setKs(e.target.checked ? [...ks, k].sort() : ks.filter((x) => x !== k))} />
            K={k}
          </label>
        ))}
        <button onClick={run} disabled={running || ks.length === 0}
                className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white disabled:bg-slate-300">
          {running ? "계산 중…" : "K별 비교 실행"}
        </button>
        <span className="text-xs text-slate-500">
          {running && elapsed != null ? `${elapsed.toFixed(0)}초 경과 · ` : ""}
          예상 약 {expectedS}초(100명 실측 기준, 최선까지 푼다)
        </span>
      </div>
      {!running && preAt != null && rows.length > 0 && (
        <PrecomputedNotice at={preAt} onRecompute={() => void run(true)}
                           hint={`처음부터 다시 푼다 — 예상 약 ${expectedS}초. 표의 시간 칸은 미리 계산 때 걸린 시간이다.`} />
      )}
      {(rows.length > 0 || running) && (
        <table className="w-full max-w-4xl text-sm">
          <thead className="text-left text-xs text-slate-500">
            <tr><th className="font-medium">K</th><th className="font-medium">빈자리</th>
                <th className="font-medium">배치 품질</th><th className="font-medium">품질 변화(K=0 대비)</th>
                <th className="font-medium">시간</th><th className="font-medium">계산 신뢰도</th><th /></tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.k} className={`border-t border-slate-100 ${pick === r.k ? "bg-indigo-50" : ""}`}>
                <td className="py-1">K={r.k}</td>
                {r.accepted ? (
                  <>
                    <td className="tabular-nums">{r.unfilled_seats}{r.unfilled_change_vs_k0 ? ` (${signed(r.unfilled_change_vs_k0, 0)})` : ""}</td>
                    <td className="tabular-nums">{r.quality?.toFixed(2)}</td>
                    <td className="tabular-nums">
                      {signed(r.quality_gain_vs_k0)}
                      {r.quality_gain_pct_vs_k0 != null && ` (${signed(r.quality_gain_pct_vs_k0, 1)}%)`}
                    </td>
                    <td className="tabular-nums">
                      {r.elapsed_s.toFixed(1)}초
                      {r.precomputed_at != null && (
                        <span className="ml-1 text-[11px] text-sky-700" title="미리 계산 때 걸린 풀이 시간(방금 계산한 시간이 아니다)">
                          (미리 계산 때)
                        </span>
                      )}
                    </td>
                    <td>
                      {r.carried_from_k != null ? (
                        // 직전 K의 해를 유지한 행: 이 K에서는 최선을 증명하지 못했다 -- 물려받은 "최선 증명"을 띄우지 않는다(리뷰 M2)
                        <span className="text-[11px] text-amber-700">
                          K={r.k} {r.solve_failed ? `풀이 실패(${r.own?.error ?? r.own?.termination ?? "원인 미상"})`
                                  : r.own?.termination === "time_limit_incumbent" ? "시간 한도로 더 나은 해를 찾지 못함"
                                  : `더 나은 해 없음(종료: ${r.own?.termination ?? "미상"})`} · K={r.carried_from_k} 해 유지(더 나빠지지 않음)
                        </span>
                      ) : (
                        <ConfidenceBadge termination={r.termination} objective={r.objective} bestBound={r.best_bound}
                                         gapAllowed={0} />
                      )}
                    </td>
                    <td><button onClick={() => setPick(r.k)} aria-label={`K=${r.k} 이동 보기`}
                                className="text-xs text-indigo-700 underline">이동 보기</button></td>
                  </>
                ) : (
                  <td colSpan={6} className="text-xs text-red-700">계산 실패: {r.error ?? r.termination ?? "해 없음"}</td>
                )}
              </tr>
            ))}
            {running && ks.filter((k) => !rows.some((r) => r.k === k)).map((k, i) => (
              // K별 실시간 송출(2026-10-11): 끝난 K는 바로 보이고, 남은 K는 계산 중으로 표시한다
              <tr key={`pending-${k}`} className="border-t border-slate-100 text-xs text-slate-400">
                <td className="py-1">K={k}</td>
                <td colSpan={6}>{i === 0 ? "계산 중…" : "대기"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {chosen && chosen.diff && (
        <div className="grid gap-4 lg:grid-cols-2">
          <MoveList diff={chosen.diff} who={who} proj={proj} title={`K=${chosen.k} 이동 그림`} />
          <div className="rounded-lg border border-slate-200 bg-white p-4 text-sm">
            <h3 className="font-semibold text-slate-900">사업별 변화(K=0 대비, 계산상)</h3>
            {Object.keys(chosen.project_change_vs_k0 ?? {}).length === 0 ? (
              <p className="mt-1 text-xs text-slate-500">K=0과 같다.</p>
            ) : (
              <ul className="mt-1 space-y-0.5">
                {Object.entries(chosen.project_change_vs_k0 ?? {}).sort((a, b) => b[1] - a[1]).map(([p, d]) => (
                  <li key={p}>{proj(p)}: <span className={d > 0 ? "text-emerald-700" : "text-red-700"}>{signed(d)}</span></li>
                ))}
              </ul>
            )}
            {(chosen.violations?.length ?? 0) > 0 && (
              <p className="mt-2 text-xs text-red-700">제약 위반: {chosen.violations!.join(", ")}</p>
            )}
          </div>
          {/* 단순 규칙 대비 카드는 여기 두지 않는다: 단순 규칙은 백지에서 전부 짜고, 이 결과는 현재 배치에서 K명만
              옮긴 것이라 같은 조건이 아니다(시연 데이터 실측: K=0 품질 69.2 대 백지 단순 규칙 76.3 -- 비교가 오해를 낳는다).
              카드는 백지에서 짠 플랜을 보는 What-if 화면에 있다. */}
        </div>
      )}
    </section>
  );
}

function MoveList({ diff, who, proj, title }: { diff: OperatingDiff; who: (id: string) => string;
                                                proj: (id: string) => string; title: string }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4 text-sm">
      <h3 className="font-semibold text-slate-900">{title}</h3>
      <p className="text-xs text-slate-500">그대로인 배치 {diff.kept}건</p>
      {diff.moved.length === 0 && diff.joined.length === 0 && <p className="mt-1 text-xs text-slate-500">이동 없음</p>}
      <ul className="mt-1 space-y-0.5">
        {diff.moved.map((m) => (
          <li key={`${m.person_id}-${m.from}`}>
            <b>{who(m.person_id)}</b>: {proj(m.from)} → {m.to.length ? m.to.map(proj).join("·") : "배치 없음"}
          </li>
        ))}
        {diff.joined.map((j) => (
          <li key={`${j.person_id}-${j.project_id}`}>
            <b>{who(j.person_id)}</b> → {proj(j.project_id)}{j.from_bench ? " (대기 인력)" : ""}
          </li>
        ))}
      </ul>
    </div>
  );
}

// --- S2: 진행 사업 보강 -------------------------------------------------------------------------

type Add = { person_id: string; project_id: string; alloc: number; grade: string; cost: number; pulled: string[] };

function Reinforce({ state, common, who, proj, fail }: Helpers) {
  const [project, setProject] = useState(state.projects[0]?.id ?? "");
  const [includePull, setIncludePull] = useState(false);
  const [budget, setBudget] = useState("");
  const [cands, setCands] = useState<Candidate[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [adds, setAdds] = useState<Add[]>([]);
  const [removes, setRemoves] = useState<[string, string][]>([]);
  const [sim, setSim] = useState<SimulateResult | null>(null);
  const [n, setN] = useState(1);
  const [pull, setPull] = useState(0);
  const [grade, setGrade] = useState("");
  const [best, setBest] = useState<BestResult | null>(null);
  const team = state.current.filter((c) => c.project_id === project);
  const budgetAdd = budget.trim() === "" ? null : Number(budget);
  const budgetValid = budgetAdd === null || (Number.isInteger(budgetAdd) && budgetAdd >= 0);
  // 결과는 그 결과를 계산한 입력(사업·조건·조합)에 묶는다 -- 계산 중 사업·조건을 바꾸면 늦게 온 응답을 버리고,
  // 받은 뒤 입력을 바꾸면 옛 결과를 숨긴다(Codex 사후 리뷰 MUST: P1 후보가 P2 표에 보이고 그대로 P2에 넣어졌다).
  const keyOf = {
    cands: JSON.stringify([project, includePull, budget.trim()]),
    sim: JSON.stringify([project, adds, removes, budget.trim()]),
    best: JSON.stringify([project, n, grade, pull, budget.trim()]),
  };
  const keysRef = useRef(keyOf);
  keysRef.current = keyOf;
  const [resultKeys, setResultKeys] = useState<{ cands?: string; sim?: string; best?: string }>({});
  const shownCands = cands && resultKeys.cands === keyOf.cands ? cands : null;
  const shownSim = sim && resultKeys.sim === keyOf.sim ? sim : null;
  const shownBest = best && resultKeys.best === keyOf.best ? best : null;

  function changeProject(p: string) {
    setProject(p); setCands(null); setAdds([]); setRemoves([]); setSim(null); setBest(null);
  }

  async function act(name: string, fn: () => Promise<void>) {
    setBusy(name);
    try { await fn(); fail(null); } catch (e) { fail(e); } finally { setBusy(null); }
  }

  const candidates = () => act("cands", async () => {
    const key = keyOf.cands;
    const res = await postStaffingCandidates(common, { project_id: project, include_pull: includePull,
                                                       budget_add: budgetAdd, top: 10 });
    if (keysRef.current.cands !== key) return;          // 그사이 사업·조건이 바뀌었다 -- 버린다
    setCands(res.candidates); setResultKeys((k) => ({ ...k, cands: key }));
  });
  const simulate = () => act("sim", async () => {
    const seats: Record<string, number> = {};
    for (const a of adds) seats[a.grade] = (seats[a.grade] ?? 0) + 1;
    const extra = budgetAdd ?? Math.ceil(adds.reduce((s, a) => s + a.cost, 0));
    const key = keyOf.sim;
    const res = await postStaffingSimulate(common, {
      project_id: project, adds: adds.map(({ person_id, project_id, alloc }) => ({ person_id, project_id, alloc })),
      removes, extra_seats: seats, budget_add: extra });
    if (keysRef.current.sim !== key) return;
    setSim(res); setResultKeys((k) => ({ ...k, sim: key }));
  });
  const bestN = () => act("best", async () => {
    const key = keyOf.best;
    const res = await postStaffingBest(common, { project_id: project, n, grade: grade || null, pull_budget: pull,
                                                 budget_add: budgetAdd });
    if (keysRef.current.best !== key) return;
    setBest(res); setResultKeys((k) => ({ ...k, best: key }));
  });

  return (
    <section className="space-y-3">
      <div>
        <h2 className="text-lg font-semibold text-slate-900">진행 사업 보강 — 누구를 넣을까</h2>
        <p className="text-sm text-slate-500">
          사업을 고르면 한 명씩 넣어 본 점수 변화 순위를 보인다. "넣어 보기"·"빼 보기"로 조합을 만들어 재평가하거나 최선 n명을 계산한다.
        </p>
      </div>
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <label>사업{" "}
          <select aria-label="보강할 사업" value={project} onChange={(e) => changeProject(e.target.value)}
                  className="rounded-md border border-slate-300 px-2 py-1">
            {state.projects.map((p) => (
              <option key={p.id} value={p.id}>{p.name}({p.id}){state.proposals.includes(p.id) ? " · 신규 제안" : ""}</option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-1">
          <input type="checkbox" checked={includePull} onChange={(e) => setIncludePull(e.target.checked)} />
          다른 사업에서 빼 오기 허용
        </label>
        <label>추가 월 예산{" "}
          <input aria-label="추가 월 예산" inputMode="numeric" value={budget} placeholder="자동(후보 비용만큼)"
                 onChange={(e) => setBudget(e.target.value)}
                 className={`w-40 rounded-md border px-2 py-1 ${budgetValid ? "border-slate-300" : "border-red-400"}`} />
        </label>
        <button onClick={candidates} disabled={!!busy || !budgetValid}
                className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white disabled:bg-slate-300">
          {busy === "cands" ? "계산 중…" : "후보 보기"}
        </button>
      </div>

      {shownCands && (
        <table className="w-full text-sm">
          <thead className="text-left text-xs text-slate-500">
            <tr><th className="font-medium">후보</th><th className="font-medium">출처</th><th className="font-medium">점수 변화</th>
                <th className="font-medium">기술 적합</th><th className="font-medium">팀 협업</th>
                <th className="font-medium">익숙한 쌍</th><th className="font-medium">빈자리(자리)</th>
                <th className="font-medium">월 비용</th><th className="font-medium">새 위반</th><th /></tr>
          </thead>
          <tbody>
            {shownCands.length === 0 && <tr><td colSpan={10} className="py-2 text-xs text-slate-500">넣을 수 있는 후보가 없다.</td></tr>}
            {shownCands.map((c) => (
              <tr key={c.person_id} className="border-t border-slate-100">
                <td className="py-1">{who(c.person_id)} · {c.grade} · 투입 {Math.round(c.alloc * 100)}%</td>
                <td>{SOURCE_LABEL[c.source]}{c.pulled_from.length ? ` (${c.pulled_from.map(proj).join(", ")})` : ""}</td>
                <td className="tabular-nums">{signed(c.delta_total)}</td>
                <td className="tabular-nums">{c.skill_fit.toFixed(2)}</td>
                <td className="tabular-nums">{c.team_synergy.toFixed(2)}</td>
                <td className="tabular-nums">{signed(c.delta.overfamiliarity)}</td>
                <td className="tabular-nums">{c.unfilled_seats_delta != null ? signed(-c.unfilled_seats_delta, 0) : signed(c.delta.unfilled)}</td>
                <td className="tabular-nums">{Math.round(c.monthly_cost).toLocaleString("ko-KR")}</td>
                <td className="text-xs text-red-700">{c.new_violations.map((v) => v[0]).join(", ")}</td>
                <td>
                  <button className="text-xs text-indigo-700 underline" aria-label={`${who(c.person_id)} 넣어 보기`}
                          disabled={adds.some((a) => a.person_id === c.person_id)}
                          onClick={() => {
                            setAdds([...adds, { person_id: c.person_id, project_id: project, alloc: c.alloc,
                                                grade: c.grade, cost: c.monthly_cost, pulled: c.pulled_from }]);
                            setRemoves([...removes, ...c.pulled_from.map((p) => [c.person_id, p] as [string, string])
                              .filter(([pp, j]) => !removes.some(([x, y]) => x === pp && y === j))]);
                          }}>
                    넣어 보기
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        <div className="rounded-lg border border-slate-200 bg-white p-4 text-sm">
          <h3 className="font-semibold text-slate-900">시뮬레이션 묶음</h3>
          <p className="mt-1 text-xs text-slate-500">지금 팀 — 빼 보기(잠긴 배치는 뺄 수 없다)</p>
          <ul className="mt-1 space-y-0.5">
            {team.map((t) => {
              const out = removes.some(([p, j]) => p === t.person_id && j === t.project_id);
              return (
                <li key={t.person_id} className={out ? "text-slate-400 line-through" : ""}>
                  {who(t.person_id)} · 투입 {Math.round(t.alloc * 100)}%
                  {t.locked ? <span className="ml-1 text-[11px] text-slate-500">(잠김)</span> : (
                    <button className="ml-2 text-xs text-indigo-700 underline"
                            aria-label={`${who(t.person_id)} ${out ? "되돌리기" : "빼 보기"}`}
                            onClick={() => setRemoves(out ? removes.filter(([p, j]) => !(p === t.person_id && j === t.project_id))
                                                          : [...removes, [t.person_id, t.project_id]])}>
                      {out ? "되돌리기" : "빼 보기"}
                    </button>
                  )}
                </li>
              );
            })}
          </ul>
          {adds.length > 0 && (
            <>
              <p className="mt-2 text-xs text-slate-500">넣을 사람</p>
              <ul className="space-y-0.5">
                {adds.map((a) => (
                  <li key={a.person_id}>
                    + {who(a.person_id)} · 투입 {Math.round(a.alloc * 100)}%
                    {a.pulled.length > 0 && <span className="text-xs text-slate-500"> ({a.pulled.map(proj).join(", ")}에서 빼 옴)</span>}
                    <button className="ml-2 text-xs text-indigo-700 underline" aria-label={`${who(a.person_id)} 넣기 취소`}
                            onClick={() => {
                              // 빼 오기로 함께 생긴 빼기도 지운다 -- 보이지 않는 빼기가 재평가에 남지 않게(리뷰 M1)
                              setAdds(adds.filter((x) => x.person_id !== a.person_id));
                              setRemoves(removes.filter(([p, j]) => !(p === a.person_id && a.pulled.includes(j))));
                            }}>빼기</button>
                  </li>
                ))}
              </ul>
            </>
          )}
          <button onClick={simulate} disabled={!!busy || !budgetValid || (adds.length === 0 && removes.length === 0)}
                  className="mt-3 rounded-md border border-slate-300 px-3 py-1 text-sm font-medium hover:bg-slate-50 disabled:opacity-50">
            {busy === "sim" ? "재평가 중…" : "재평가"}
          </button>
          {shownSim && <PartsTable before={shownSim.before} after={shownSim.after} delta={shownSim.delta} newViolations={shownSim.new_violations} />}
        </div>

        <div className="rounded-lg border border-slate-200 bg-white p-4 text-sm">
          <h3 className="font-semibold text-slate-900">최선 n명</h3>
          <div className="mt-2 flex flex-wrap items-center gap-3">
            <label>인원{" "}
              <select aria-label="추가 인원" value={n} onChange={(e) => setN(Number(e.target.value))}
                      className="rounded-md border border-slate-300 px-2 py-1">
                {[1, 2, 3].map((x) => <option key={x} value={x}>{x}명</option>)}
              </select>
            </label>
            <label>등급{" "}
              <select aria-label="추가 등급" value={grade} onChange={(e) => setGrade(e.target.value)}
                      className="rounded-md border border-slate-300 px-2 py-1">
                <option value="">상관없음</option>
                {["특급", "고급", "중급", "초급"].map((g) => <option key={g} value={g}>{g}</option>)}
              </select>
            </label>
            <label>다른 사업에서 빼 오기 최대{" "}
              <select aria-label="빼 오기 최대" value={pull} onChange={(e) => setPull(Number(e.target.value))}
                      className="rounded-md border border-slate-300 px-2 py-1">
                {[0, 1, 2].map((x) => <option key={x} value={x}>{x}명</option>)}
              </select>
            </label>
            <button onClick={bestN} disabled={!!busy || !budgetValid}
                    className="rounded-md border border-slate-300 px-3 py-1 text-sm font-medium hover:bg-slate-50 disabled:opacity-50">
              {busy === "best" ? "계산 중…" : "최선 조합 계산"}
            </button>
          </div>
          {shownBest && !shownBest.accepted && <p className="mt-2 text-xs text-red-700">해를 찾지 못했다({shownBest.termination ?? "원인 미상"}).</p>}
          {shownBest && shownBest.accepted && shownBest.diff && (
            <>
              <MoveList diff={shownBest.diff} who={who} proj={proj} title={`최선 ${n}명 — 바뀐 배치`} />
              <div className="mt-1"><ConfidenceBadge termination={shownBest.termination} gapAllowed={0} /></div>
              <p className="mt-1 text-xs text-slate-600">추가 월 비용 {Math.round(shownBest.added_cost ?? 0).toLocaleString("ko-KR")}</p>
              {shownBest.before && shownBest.after && shownBest.delta && (
                <PartsTable before={shownBest.before} after={shownBest.after} delta={shownBest.delta}
                            newViolations={(shownBest.violations ?? []).map((v) => [v.code, v.location] as [string, string])} />
              )}
            </>
          )}
        </div>
      </div>
    </section>
  );
}

function PartsTable({ before, after, delta, newViolations }: { before: Parts; after: Parts; delta: Parts;
                                                              newViolations: [string, string][] }) {
  return (
    <>
      <table className="mt-2 w-full text-xs">
        <thead className="text-left text-slate-500">
          <tr><th className="font-medium">항목</th><th className="font-medium">전</th><th className="font-medium">후</th>
              <th className="font-medium">변화(계산상)</th></tr>
        </thead>
        <tbody>
          {(Object.keys(PART_LABEL) as (keyof Parts)[]).map((k) => (
            <tr key={k} className="border-t border-slate-100">
              <td className="py-0.5">{PART_LABEL[k]}</td>
              <td className="tabular-nums">{before[k].toFixed(2)}</td>
              <td className="tabular-nums">{after[k].toFixed(2)}</td>
              <td className={`tabular-nums ${delta[k] > 1e-9 ? "text-emerald-700" : delta[k] < -1e-9 ? "text-red-700" : ""}`}>
                {signed(delta[k])}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {newViolations.length > 0 && (
        <p className="mt-1 text-xs text-red-700">새 위반: {newViolations.map(([c, l]) => `${c}(${l})`).join(", ")}</p>
      )}
    </>
  );
}
