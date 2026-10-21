/** 지금 배치 대비 명단 차이(2026-10-21 -- 화면 검토 P1-2 "운영 중 안 A~D가 지금 대비 몇 명을 옮기는지"와 새 What-if의 바꾼 뒤 명단).
 *  사람 단위로 센다: 옮김 = 지금도 배치돼 있는데 사업이 바뀜, 새로 = 지금 배치가 없던 사람, 빠짐 = 지금 배치돼 있는데 명단에서 빠짐.
 *  서버 확정 미리 계산(`/api/workspace/preview`)과 같은 정의다. */
type E = { person_id: string; project_id: string };

export interface RosterDiff {
  moved: string[];
  joined: string[];
  left: string[];
  /** 지금 배치에 없던 (사람, 사업) -- "사람|사업" → 새로(배치 없던 사람) · 옮김(원래 사업에서) · 추가(원래 사업은 그대로) */
  tag: Map<string, { kind: "new" | "moved" | "added"; from: string[] }>;
  /** 사업 → 그 사업에서 빠진 사람 */
  leftBy: Map<string, string[]>;
}

export function rosterDiff(base: E[], now: E[]): RosterDiff {
  const by = (es: E[]) => {
    const m = new Map<string, Set<string>>();
    for (const e of es) m.set(e.person_id, (m.get(e.person_id) ?? new Set()).add(e.project_id));
    return m;
  };
  const pb = by(base), pa = by(now);
  const same = (a: Set<string>, b: Set<string>) => a.size === b.size && [...a].every((x) => b.has(x));
  const moved = [...pa.keys()].filter((p) => pb.has(p) && !same(pb.get(p)!, pa.get(p)!)).sort();
  const joined = [...pa.keys()].filter((p) => !pb.has(p)).sort();
  const left = [...pb.keys()].filter((p) => !pa.has(p)).sort();
  const had = new Set(base.map((e) => `${e.person_id}|${e.project_id}`));
  const has = new Set(now.map((e) => `${e.person_id}|${e.project_id}`));
  const tag: RosterDiff["tag"] = new Map();
  for (const e of now) {
    const k = `${e.person_id}|${e.project_id}`;
    if (had.has(k)) continue;
    const before = pb.get(e.person_id);
    if (!before) { tag.set(k, { kind: "new", from: [] }); continue; }
    const from = [...before].filter((j) => !pa.get(e.person_id)!.has(j)).sort();
    tag.set(k, { kind: from.length ? "moved" : "added", from });
  }
  const leftBy: RosterDiff["leftBy"] = new Map();
  for (const e of base) {
    if (has.has(`${e.person_id}|${e.project_id}`)) continue;
    leftBy.set(e.project_id, [...(leftBy.get(e.project_id) ?? []), e.person_id]);
  }
  return { moved, joined, left, tag, leftBy };
}

/** "지금 대비 옮김 3명 · 새로 2명 · 빠짐 1명" */
export function diffText(d: RosterDiff): string {
  return `옮김 ${d.moved.length}명 · 새로 ${d.joined.length}명 · 빠짐 ${d.left.length}명`;
}
