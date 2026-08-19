import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { SwapControl } from "./SwapControl";
import { entryKey, parseEntryKey, resolveDrop } from "./swapRules";
import type { Person, AssignEntry } from "../api/types";

const people: Person[] = [
  { id: "p000", name: "김", grade: "중급", skills: {} },
  { id: "p001", name: "이", grade: "고급", skills: {} },
  { id: "p002", name: "박", grade: "초급", skills: {} },
];
const entries: AssignEntry[] = [{ person_id: "p000", project_id: "j00", alloc: 1 }];

describe("SwapControl", () => {
  it("교체 후보에서 이미 배치된 사람은 제외한다", () => {
    render(<SwapControl people={people} entries={entries} onSwap={() => {}} busy={false} />);
    const options = screen.getByLabelText("교체 투입").querySelectorAll("option");
    const ids = [...options].map((o) => o.getAttribute("value"));
    expect(ids).not.toContain("p000");   // 이미 배치됨
    expect(ids).toContain("p001");
  });

  it("두 사람이 정해지면 스왑을 알린다", () => {
    const onSwap = vi.fn();
    render(<SwapControl people={people} entries={entries} onSwap={onSwap} busy={false} />);
    fireEvent.change(screen.getByLabelText("교체 대상"), { target: { value: "p000::j00" } });
    fireEvent.change(screen.getByLabelText("교체 투입"), { target: { value: "p001" } });
    fireEvent.click(screen.getByRole("button", { name: /브리핑/ }));
    expect(onSwap).toHaveBeenCalledWith({
      out_person_id: "p000", in_person_id: "p001", project_id: "j00",
    });
  });

  it("같은 사람이 두 프로젝트에 배치되면 프로젝트별로 구분해 고를 수 있다", () => {
    // 실제 Plan A는 112 entries / 78명 -- 25명이 2개 이상 프로젝트에 걸쳐 있다.
    // person_id만으로 옵션을 만들면 값이 겹쳐서 어느 프로젝트를 교체하는지 알 수
    // 없고, entries.find(person_id)는 항상 첫 번째 항목을 집는다.
    const onSwap = vi.fn();
    const multi: AssignEntry[] = [
      { person_id: "p000", project_id: "j00", alloc: 1 },
      { person_id: "p000", project_id: "j01", alloc: 1 },
    ];
    render(<SwapControl people={people} entries={multi} onSwap={onSwap} busy={false} />);
    const opts = [...screen.getByLabelText("교체 대상").querySelectorAll("option")]
      .filter((o) => o.value !== "");
    expect(new Set(opts.map((o) => o.value)).size).toBe(2);

    fireEvent.change(screen.getByLabelText("교체 대상"), { target: { value: opts[1].value } });
    fireEvent.change(screen.getByLabelText("교체 투입"), { target: { value: "p001" } });
    fireEvent.click(screen.getByRole("button", { name: /브리핑/ }));
    expect(onSwap).toHaveBeenCalledWith({
      out_person_id: "p000", in_person_id: "p001", project_id: "j01",
    });
  });
});

describe("resolveDrop — 드래그 드롭 규칙", () => {
  it("배치 칩을 '교체 대상' 슬롯에 떨어뜨리면 그 (사람,프로젝트) 쌍을 수락한다", () => {
    expect(resolveDrop("out::p000::j00", "out"))
      .toEqual({ slot: "out", value: "p000::j00" });
  });

  it("대기 칩을 '교체 투입' 슬롯에 떨어뜨리면 수락한다", () => {
    expect(resolveDrop("in::p001", "in")).toEqual({ slot: "in", value: "p001" });
  });

  it("배치 칩은 '교체 투입' 슬롯에 들어갈 수 없다", () => {
    // 셀렉트는 bench로 필터링해 이 경우를 못 고르게 한다.
    // 드래그 경로에도 같은 규칙이 걸려야 두 입력이 같은 결과를 낸다.
    expect(resolveDrop("out::p000::j00", "in")).toBeNull();
  });

  it("대기 칩은 '교체 대상' 슬롯에 들어갈 수 없다", () => {
    expect(resolveDrop("in::p001", "out")).toBeNull();
  });

  it("'교체 대상'은 프로젝트가 빠진 값을 거부한다", () => {
    expect(resolveDrop("out::p000", "out")).toBeNull();
  });
});

describe("entryKey / parseEntryKey", () => {
  it("왕복 변환이 보존된다", () => {
    expect(parseEntryKey(entryKey("p000", "j01")))
      .toEqual({ personId: "p000", projectId: "j01" });
  });

  it("형식이 아니면 null", () => {
    expect(parseEntryKey("p000")).toBeNull();
  });
});
