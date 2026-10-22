import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { BudgetRaiseBox, raiseBody, raisesReady } from "./GradeSwapBox";
import { Steps } from "../ui";

describe("예산 증액 사유(2026-10-22 P3-2)·단계 표시(P2-3)", () => {
  it("넘친 금액마다 사유를 받아야 확정할 수 있고, 금액은 미리 계산 그대로 보낸다", () => {
    const raises = { J019: 950 };
    expect(raisesReady(undefined, {})).toBe(true);
    expect(raisesReady(raises, {})).toBe(false);
    expect(raisesReady(raises, { J019: "  " })).toBe(false);
    expect(raiseBody(raises, { J019: " 고객 요청 " })).toEqual([{ project_id: "J019", amount: 950, reason: "고객 요청" }]);
    let got = "";
    render(<BudgetRaiseBox raises={raises} reasons={{}} onReason={(_j, v) => { got = v; }} />);
    expect(screen.getByText(/증액 사유 1건을 적어야/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("J019 예산 증액 사유"), { target: { value: "범위 확대" } });
    expect(got).toBe("범위 확대");
  });

  it("단계 표시: 지난 단계 ✓, 지금 단계 aria-current, 다음 할 일 버튼", () => {
    render(<Steps label="시험 단계" steps={["등록", "후보", "확정"]} current={1} next={<button>다음</button>} />);
    const nav = screen.getByRole("navigation", { name: "시험 단계" });
    expect(nav.querySelector('[aria-current="step"]')).toHaveTextContent("② 후보");
    expect(nav).toHaveTextContent("✓ 등록");
    expect(screen.getByRole("button", { name: "다음" })).toBeInTheDocument();
  });
});
