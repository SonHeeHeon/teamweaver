import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { ApplyControl } from "./ApplyControl";
import type { WhatifResponse } from "../api/types";

const SWAP = { out_person_id: "p1", in_person_id: "p2", project_id: "j1" };
const nameOf = (id: string) => ({ p1: "김일번", p2: "이이번" } as Record<string, string>)[id] ?? id;

const zero = { skill: 0, synergy: 0, overfamiliarity: 0, unfilled: 0, total: 0 };
const CLEAN: WhatifResponse = { objective_delta: 0.1, before: zero, after: zero, new_violations: [],
  new_shortfalls: [], feasible: true, fallback_used: true,
  briefing: { rationale: "", risks: [], alternatives: [] } };
const RISKY: WhatifResponse = { ...CLEAN, feasible: false,
  new_violations: [{ code: "budget", location: "j1@0", actual: 2, limit: 1,
                     message: "j1 0월 예산 초과" }] };

describe("ApplyControl", () => {
  it("문제가 없으면 한 번에 적용한다", () => {
    const onApply = vi.fn();
    render(<ApplyControl result={CLEAN} swap={SWAP} nameOf={nameOf} busy={false} onApply={onApply} />);
    expect(screen.getByText("검토한 교체: 김일번 → 이이번 (j1)")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "이 교체 적용" }));
    expect(onApply).toHaveBeenCalledTimes(1);
  });

  it("위반이 있으면 경고를 보여 주고 '위반을 알고 적용'을 눌러야 적용한다", () => {
    const onApply = vi.fn();
    render(<ApplyControl result={RISKY} swap={SWAP} nameOf={nameOf} busy={false} onApply={onApply} />);
    fireEvent.click(screen.getByRole("button", { name: /경고 있음/ }));
    expect(onApply).not.toHaveBeenCalled();
    expect(screen.getByRole("alertdialog")).toHaveTextContent("j1 0월 예산 초과");
    fireEvent.click(screen.getByRole("button", { name: "위반을 알고 적용" }));
    expect(onApply).toHaveBeenCalledTimes(1);
  });

  it("경고 단계에서 취소하면 적용하지 않는다", () => {
    const onApply = vi.fn();
    render(<ApplyControl result={RISKY} swap={SWAP} nameOf={nameOf} busy={false} onApply={onApply} />);
    fireEvent.click(screen.getByRole("button", { name: /경고 있음/ }));
    fireEvent.click(screen.getByRole("button", { name: "취소" }));
    expect(onApply).not.toHaveBeenCalled();
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });

  it("검토 결과가 없으면 아무것도 보이지 않는다", () => {
    const { container } = render(<ApplyControl result={null} swap={null} nameOf={nameOf} busy={false} onApply={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();
  });
});
