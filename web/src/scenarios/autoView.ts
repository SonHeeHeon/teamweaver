/** 자동 편성 K 행의 설명(2026-10-21 사용자 요청 1 "k=n일때 각 row에서 신규 사업에 대해 각각 어떻게 편성을 했는지 … 간단 명료하게").
 *  서버 K 행에 이미 있는 새 배치(entries)·이동(diff)만으로 만든다 -- 계산을 다시 하지 않는다.
 *  - 사업별 구성: 신규 사업마다 몇 명이 어디서 왔는지(대기 · 이동 · 원래 팀원 · 동시 수행)
 *  - 옮김 사슬: 한 사람이 신규 사업으로 가면 그 빈자리를 다른 진행 사업 사람이 메운다(J007 → J015 → J020)
 *  - 직전 K와 다른 점: K를 하나 늘려 새로 옮긴 사람 */
import type { AssignEntry, OperatingDiff } from "../api/types";

export interface AutoRow { k: number; entries?: AssignEntry[]; diff?: OperatingDiff; carried_from_k?: number }
export interface PipelineSeats { project_id: string; seats: Record<string, { need: number }> }
export type Origin = "bench" | "moved" | "kept" | "concurrent";
export interface Member { person_id: string; name: string; origin: Origin; from?: string }
export interface ProjectMix { project_id: string; size: number; need: number; members: Member[];
                              counts: Record<Origin, number> }
export interface Step { from: string; person_id: string; name: string; to: string }

const ORIGIN_LABEL: Record<Origin, string> = { bench: "대기", moved: "이동", kept: "원래", concurrent: "동시 수행" };

/** 신규 사업마다 팀 구성. 사람 출처: 대기 인력(from_bench) · 진행 사업에서 이동(diff.moved) · 원래 이 사업에 있던 사람 ·
 *  원래 사업을 유지하며 더해진 사람(프로젝트 동시 수행). */
export function projectMix(row: AutoRow, pipeline: PipelineSeats[], name: (id: string) => string): ProjectMix[] {
  const entries = row.entries ?? [];
  const joined = new Map((row.diff?.joined ?? []).map((j) => [`${j.person_id}@${j.project_id}`, j]));
  const moved = new Map<string, { from: string; to: string[] }>();
  for (const m of row.diff?.moved ?? []) if (!moved.has(m.person_id)) moved.set(m.person_id, m);
  return pipeline.map((p) => {
    const members: Member[] = entries.filter((e) => e.project_id === p.project_id).map((e) => {
      const j = joined.get(`${e.person_id}@${p.project_id}`);
      const m = moved.get(e.person_id);
      const origin: Origin = j?.from_bench ? "bench" : m && m.to.includes(p.project_id) ? "moved" : j ? "concurrent" : "kept";
      return { person_id: e.person_id, name: name(e.person_id), origin, ...(origin === "moved" ? { from: m!.from } : {}) };
    });
    const counts = { bench: 0, moved: 0, kept: 0, concurrent: 0 } as Record<Origin, number>;
    for (const m of members) counts[m.origin] += 1;
    const need = Object.values(p.seats ?? {}).reduce((s, x) => s + (x.need ?? 0), 0);
    return { project_id: p.project_id, size: members.length, need, members, counts };
  });
}

/** "4/4명 · 대기 3 · 이동 1" -- 정원이 없으면 "4명". 0인 출처는 뺀다. */
export function mixText(m: ProjectMix): string {
  const head = m.need ? `${m.size}/${m.need}명` : `${m.size}명`;
  const parts = (Object.keys(ORIGIN_LABEL) as Origin[]).filter((o) => m.counts[o] > 0).map((o) => `${ORIGIN_LABEL[o]} ${m.counts[o]}`);
  return [head, ...parts].join(" · ");
}

/** 옮김을 사슬로 잇는다: 출발 사업이 다른 이동의 도착지가 아닌 이동에서 시작해, 도착 사업에서 다시 빠져나간 사람을 따라간다. */
export function moveChains(row: AutoRow, name: (id: string) => string): Step[][] {
  const steps: Step[] = (row.diff?.moved ?? []).flatMap((m) =>
    (m.to.length ? m.to : ["대기"]).slice(0, 1).map((to) => ({ from: m.from, person_id: m.person_id, name: name(m.person_id), to })));
  const used = new Set<number>();
  const arrivals = new Set(steps.map((s) => s.to));
  const chains: Step[][] = [];
  const follow = (first: number) => {
    const chain = [steps[first]];
    used.add(first);
    for (;;) {
      const next = steps.findIndex((s, i) => !used.has(i) && s.from === chain[chain.length - 1].to);
      if (next < 0) break;
      used.add(next);
      chain.push(steps[next]);
    }
    chains.push(chain);
  };
  steps.forEach((s, i) => { if (!used.has(i) && !arrivals.has(s.from)) follow(i); });
  steps.forEach((_, i) => { if (!used.has(i)) follow(i); });            // 돌고 도는 이동(맞바꿈)도 빠뜨리지 않는다
  return chains;
}

/** "J007 →(강민) J015 →(윤소율) J020" */
export function chainText(chain: Step[]): string {
  return chain.reduce((s, st) => `${s} →(${st.name}) ${st.to}`, chain[0]?.from ?? "");
}

/** 직전 K 행과 견줘 새로 옮긴 사람·이번엔 옮기지 않은 사람. 직전 해를 그대로 쓴 행(carried)이면 바뀐 것이 없다. */
export function changesFromPrev(row: AutoRow, prev: AutoRow | undefined, name: (id: string) => string): { added: string[]; dropped: string[] } {
  if (!prev || row.carried_from_k !== undefined) return { added: [], dropped: [] };
  const key = (m: { person_id: string; from: string; to: string[] }) => `${m.person_id}|${m.from}|${m.to.join(",")}`;
  const before = new Map((prev.diff?.moved ?? []).map((m) => [key(m), m]));
  const now = new Map((row.diff?.moved ?? []).map((m) => [key(m), m]));
  const text = (m: { person_id: string; from: string; to: string[] }) => `${name(m.person_id)} ${m.from}→${m.to.join("·") || "대기"}`;
  return { added: [...now].filter(([k]) => !before.has(k)).map(([, m]) => text(m)),
           dropped: [...before].filter(([k]) => !now.has(k)).map(([, m]) => text(m)) };
}
