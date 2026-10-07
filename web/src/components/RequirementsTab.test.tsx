import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { RequirementsTab } from "./RequirementsTab";
import type { Meta } from "../api/types";

const META: Meta = {
  people: [{ id: "p000", name: "김나윤", grade: "중급", skills: { React: 2 } }],
  projects: [{ id: "j00", name: "차세대 포털", sector: "대내", phase: "실행",
               start_month: 0, end_month: 3, grade_headcount: { 중급: 2 },
               monthly_budget: 5000 }],
  skills: ["React", "Java"],
  review_items: [],
  coworks: [],
  dataset_version: "a".repeat(64),
};

describe("RequirementsTab", () => {
  it("모든 스킬에 슬라이더를 그리고 기본값 3을 보인다", () => {
    render(<RequirementsTab meta={META} weights={{}} onWeightsChange={() => {}}
                            onRun={() => {}} running={false} />);
    expect(screen.getByLabelText("React")).toHaveValue("3");
    expect(screen.getByLabelText("Java")).toHaveValue("3");
  });

  it("슬라이더를 움직이면 가중치 변경을 알린다", () => {
    const onWeightsChange = vi.fn();
    render(<RequirementsTab meta={META} weights={{}} onWeightsChange={onWeightsChange}
                            onRun={() => {}} running={false} />);
    fireEvent.change(screen.getByLabelText("React"), { target: { value: "5" } });
    expect(onWeightsChange).toHaveBeenCalledWith({ React: 5 });
  });

  it("프로젝트 제약(정원·예산)을 보여준다", () => {
    render(<RequirementsTab meta={META} weights={{}} onWeightsChange={() => {}}
                            onRun={() => {}} running={false} />);
    expect(screen.getByText("차세대 포털")).toBeInTheDocument();
    expect(screen.getByText(/5,000/)).toBeInTheDocument();
  });

  it("실행 중에는 버튼이 잠긴다", () => {
    render(<RequirementsTab meta={META} weights={{}} onWeightsChange={() => {}}
                            onRun={() => {}} running={true} />);
    expect(screen.getByRole("button", { name: /최적화/ })).toBeDisabled();
  });

  it("운영 중 데이터면 '전부 다시 짜기'의 한계와 운영 중 편성 탭을 안내한다", () => {
    const open = vi.fn();
    const { rerender } = render(<RequirementsTab meta={META} weights={{}} onWeightsChange={() => {}}
                                                 onRun={() => {}} running={false} />);
    expect(screen.queryByRole("note")).not.toBeInTheDocument();
    rerender(<RequirementsTab meta={META} weights={{}} onWeightsChange={() => {}} onRun={() => {}} running={false}
                              operating onOpenOperating={open} />);
    expect(screen.getByRole("note")).toHaveTextContent(/빈자리가 남을 수 있다/);
    fireEvent.click(screen.getByRole("button", { name: "운영 중 편성 탭" }));
    expect(open).toHaveBeenCalled();
  });
});
