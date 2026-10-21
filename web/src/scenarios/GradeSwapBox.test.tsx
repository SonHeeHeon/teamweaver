import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { GradeSwapBox, hintBody, swapBody, swapDirection, swapList, swapsReady } from "./GradeSwapBox";
import { hintText } from "./pickView";

const up = { seat: "중급" as const, grade: "고급" as const, count: 1 };
const down = { seat: "중급" as const, grade: "초급" as const, count: 1 };

describe("등급 대체(사용자 결정 2026-10-21: 위·아래 모두, 사유 필수, 예산 넘으면 증액)", () => {
  it("대체 줄마다 사유 칸 -- 다 적어야 준비됨, 아래 등급 대체는 따로 표시", () => {
    const items = [{ projectId: "J021", swap: up }, { projectId: "J021", swap: down }];
    const onReason = vi.fn();
    const { rerender } = render(<GradeSwapBox items={items} reasons={{}} onReason={onReason} budgetAdd={1200} />);
    expect(screen.getByText(/사유 2건을 적어야 확정할 수 있습니다/)).toBeInTheDocument();
    expect(screen.getByText("(아래 등급 대체)", { exact: false })).toBeInTheDocument();
    expect(screen.getByText(/월 예산 \+1,200/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("J021 중급 자리 고급 대체 사유"), { target: { value: "중급 전원 투입 중" } });
    expect(onReason).toHaveBeenCalledWith("J021:중급>고급", "중급 전원 투입 중");
    expect(swapsReady(items, { "J021:중급>고급": "중급 전원 투입 중" })).toBe(false);
    const reasons = { "J021:중급>고급": " 중급 전원 투입 중 ", "J021:중급>초급": "범위상 충분" };
    expect(swapsReady(items, reasons)).toBe(true);
    rerender(<GradeSwapBox items={items} reasons={reasons} onReason={onReason} />);
    expect(screen.getByText(/사유를 모두 적었습니다/)).toBeInTheDocument();
    expect(swapBody(items, reasons)[0]).toEqual({ project_id: "J021", seat: "중급", grade: "고급", count: 1, reason: "중급 전원 투입 중" });
    expect(swapDirection(up)).toBe("up");
    expect(swapDirection(down)).toBe("down");
  });

  it("대체가 없으면 아무것도 그리지 않고, 긴급 투입 apply_hint는 사유를 붙인 목록으로 바꿔 보낸다", () => {
    const { container } = render(<GradeSwapBox items={[]} reasons={{}} onReason={() => {}} />);
    expect(container.firstChild).toBeNull();
    const hint = { headcount_add: { J007: { 고급: 1 } }, budget_add: { J007: 500 }, skills_add: {}, grade_swaps: { J007: [up] } };
    expect(swapList(hint.grade_swaps)).toEqual([{ projectId: "J007", swap: up }]);
    expect(hintBody(hint, { "J007:중급>고급": "급히" })).toEqual({
      headcount_add: { J007: { 고급: 1 } }, budget_add: { J007: 500 }, skills_add: {},
      grade_swaps: [{ project_id: "J007", seat: "중급", grade: "고급", count: 1, reason: "급히" }] });
    const { grade_swaps: _, ...plain } = hint;
    expect(hintBody(plain, {})).toEqual(plain);                      // 대체가 없으면 칸을 보내지 않는다
    expect(hintText(hint, "확정하면")).toContain("등급 대체: 중급 자리 1개 → 고급(사유 필요)");
    expect(hintText({ headcount_add: {}, budget_add: {}, skills_add: {}, grade_swaps: { J007: [down] } }, "확정하면"))
      .toBe("확정하면 등급 대체: 중급 자리 1개 → 초급(사유 필요).");
  });
});
