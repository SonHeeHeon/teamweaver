import { describe, expect, it } from "vitest";
import { duosText, partnersText, seatLines, skillCollab, violationReasons, withGwa } from "./pickView";
import type { CandidateRow } from "./workspaceApi";

describe("pickView -- 정원 변화·협업 글(사용자 2026-10-20 요청 4·5번)", () => {
  it("정원 변화는 사업별로, 이 사업이 먼저", () => {
    expect(seatLines([{ project_id: "J013", name: "x", change: -1 }, { project_id: "J018", name: "y", change: 1 }], "J018"))
      .toEqual([{ text: "이 사업 1자리 채움", tone: "good", title: "y" }, { text: "J013에 1자리 빔", tone: "warn", title: "x" }]);
    expect(seatLines(undefined, "J018")).toEqual([]);
  });
  it("협업 쌍은 누구와인지 조사와 함께", () => {
    expect(withGwa("이보람")).toBe("이보람과");
    expect(withGwa("김은우")).toBe("김은우와");
    expect(withGwa("DP0055")).toBe("DP0055와");
    expect(partnersText({ delta_total: 0, partners: [{ person_id: "P3", name: "박서준", kind: "familiar", value: -0.5 }] } as CandidateRow))
      .toBe("박서준과 -0.50(익숙한 쌍)");
  });
  it("예전 응답(나눔 없음)은 기술 칸에 합친 값, 협업은 비운다", () => {
    const old = { delta_total: 3.1, delta_excl_unfilled: 1.2 } as CandidateRow;
    expect(skillCollab(old)).toEqual({ skill: 1.2, collab: undefined });
    expect(skillCollab({ ...old, skill_delta: 1, collab_delta: 0.2 })).toEqual({ skill: 1, collab: 0.2 });
    expect(partnersText({ ...old, partners: [] })).toBeNull();
  });
  it("한 사람과의 보상·감점은 한 번에, 위반은 이유와 함께", () => {
    const r = { delta_total: 0, partners: [{ person_id: "P1", name: "이보람", kind: "synergy", value: 0.19 },
                                          { person_id: "P1", name: "이보람", kind: "familiar", value: -0.2 }] } as CandidateRow;
    expect(partnersText(r)).toBe("이보람과 +0.19·익숙한 쌍 -0.20");
    expect(duosText([{ a: "P1", a_name: "이보람", b: "P2", b_name: "윤시현", kind: "synergy", value: 0.17 },
                     { a: "P1", a_name: "이보람", b: "P2", b_name: "윤시현", kind: "familiar", value: -0.2 }]))
      .toBe("이보람·윤시현 +0.17·익숙한 쌍 -0.20");
    expect(violationReasons([["grade_over", "J018:특급"], ["budget", "J018"], ["grade_over", "J018:고급"]])).toBe("등급 정원 초과, 예산 초과");
  });
});
