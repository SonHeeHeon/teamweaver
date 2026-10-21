import { describe, expect, it } from "vitest";
import { diffText, rosterDiff } from "./rosterDiff";

describe("지금 배치 대비 명단 차이(2026-10-21)", () => {
  it("옮김·새로·빠짐·추가를 사람 단위로 센다", () => {
    const base = [{ person_id: "a", project_id: "J1" }, { person_id: "b", project_id: "J1" }, { person_id: "c", project_id: "J2" },
                  { person_id: "d", project_id: "J2" }];
    const now = [{ person_id: "a", project_id: "J2" },                       // a: J1 → J2 옮김
                 { person_id: "b", project_id: "J1" },                       // b: 그대로
                 { person_id: "c", project_id: "J2" }, { person_id: "c", project_id: "J3" },   // c: J2 그대로 + J3 추가
                 { person_id: "e", project_id: "J1" }];                      // e: 새로, d: 빠짐
    const d = rosterDiff(base, now);
    expect(d.moved).toEqual(["a", "c"]);
    expect(d.joined).toEqual(["e"]);
    expect(d.left).toEqual(["d"]);
    expect(d.tag.get("a|J2")).toEqual({ kind: "moved", from: ["J1"] });
    expect(d.tag.get("c|J3")).toEqual({ kind: "added", from: [] });
    expect(d.tag.get("e|J1")).toEqual({ kind: "new", from: [] });
    expect(d.tag.has("b|J1")).toBe(false);
    expect(d.leftBy.get("J1")).toEqual(["a"]);
    expect(d.leftBy.get("J2")).toEqual(["d"]);
    expect(diffText(d)).toBe("옮김 2명 · 새로 1명 · 빠짐 1명");
  });
});
