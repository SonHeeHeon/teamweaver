import { describe, expect, it } from "vitest";
import { chainText, changesFromPrev, mixText, moveChains, projectMix, type AutoRow, type PipelineSeats } from "./autoView";

const NAMES: Record<string, string> = { A: "강민", B: "윤소율", C: "오세린", D: "김가온", E: "이나래", F: "박다솜" };
const name = (id: string) => NAMES[id] ?? id;
const PIPE: PipelineSeats[] = [{ project_id: "J020", seats: { 중급: { need: 2 } } }, { project_id: "J019", seats: { 고급: { need: 1 }, 초급: { need: 2 } } }];

// 연말형 시연 K=3 모양: 대기 투입 + 사슬(J007 → J015 → J020) + 단독 이동(J008 → J019)
const K3: AutoRow = {
  k: 3,
  entries: [
    { person_id: "D", project_id: "J020", alloc: 1 }, { person_id: "B", project_id: "J020", alloc: 1 },
    { person_id: "E", project_id: "J019", alloc: 1 }, { person_id: "C", project_id: "J019", alloc: 1 },
    { person_id: "A", project_id: "J015", alloc: 1 }, { person_id: "F", project_id: "J019", alloc: 1 },
  ],
  diff: {
    kept: 80,
    moved: [{ person_id: "A", from: "J007", to: ["J015"] }, { person_id: "B", from: "J015", to: ["J020"] },
            { person_id: "C", from: "J008", to: ["J019"] }],
    joined: [{ person_id: "D", project_id: "J020", from_bench: true }, { person_id: "E", project_id: "J019", from_bench: true },
             { person_id: "F", project_id: "J019", from_bench: false }],
  },
};

describe("autoView -- 자동 편성 K 행 설명(사용자 2026-10-21 요청 1)", () => {
  it("신규 사업마다 몇 명이 어디서 왔는지", () => {
    const [j020, j019] = projectMix(K3, PIPE, name);
    expect(mixText(j020)).toBe("2/2명 · 대기 1 · 이동 1");
    expect(j020.members.find((m) => m.person_id === "B")).toMatchObject({ origin: "moved", from: "J015", name: "윤소율" });
    // 원래 사업을 유지하며 더해진 사람(from_bench 아님, 이동 아님) = 동시 수행
    expect(mixText(j019)).toBe("3/3명 · 대기 1 · 이동 1 · 동시 수행 1");
  });
  it("정원 없는 사업·빈 사업", () => {
    expect(mixText(projectMix({ k: 0, entries: [], diff: { kept: 0, moved: [], joined: [] } }, [{ project_id: "J1", seats: {} }], name)[0]))
      .toBe("0명");
  });
  it("옮김은 사슬로 잇는다", () => {
    const chains = moveChains(K3, name);
    expect(chains.map(chainText)).toEqual(["J007 →(강민) J015 →(윤소율) J020", "J008 →(오세린) J019"]);
  });
  it("맞바꿈(돌고 도는 이동)도 빠뜨리지 않는다", () => {
    const swap: AutoRow = { k: 2, diff: { kept: 0, joined: [], moved: [
      { person_id: "A", from: "J1", to: ["J2"] }, { person_id: "B", from: "J2", to: ["J1"] }] } };
    expect(moveChains(swap, name).map(chainText)).toEqual(["J1 →(강민) J2 →(윤소율) J1"]);
  });
  it("직전 K보다 새로 옮긴 사람", () => {
    const k2: AutoRow = { k: 2, diff: { kept: 0, joined: [], moved: [K3.diff!.moved[1], K3.diff!.moved[2]] } };
    expect(changesFromPrev(K3, k2, name)).toEqual({ added: ["강민 J007→J015"], dropped: [] });
    expect(changesFromPrev({ ...K3, carried_from_k: 2 }, k2, name)).toEqual({ added: [], dropped: [] });
    expect(changesFromPrev(K3, undefined, name)).toEqual({ added: [], dropped: [] });
  });
});
