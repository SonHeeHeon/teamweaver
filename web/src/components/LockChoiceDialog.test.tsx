import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import type { Meta } from "../api/types";
import { LockChoiceDialog } from "./LockChoiceDialog";

const META = {
  people: [{ id: "P1", name: "가나다" }], projects: [{ id: "J1", name: "보험사 운영" }], skills: [], review_items: [], coworks: [],
  dataset_version: "v1", locked_assignments: [{ person_id: "P1", project_id: "J1" }],
} as unknown as Meta;

describe("연간 계획 계산 전 고정 선택(2026-10-21)", () => {
  it("고정한 배치를 보이고 유지·풀기·취소를 고른다", () => {
    const on = { onKeep: vi.fn(), onRelease: vi.fn(), onClose: vi.fn() };
    render(<LockChoiceDialog meta={META} {...on} />);
    expect(screen.getByRole("dialog", { name: "고정한 배치 1건 — 어떻게 짤까요?" })).toBeInTheDocument();
    expect(screen.getByText("가나다 — J1 보험사 운영")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "고정 유지하고 짜기" }));
    fireEvent.click(screen.getByRole("button", { name: "고정 풀고 다시 배치" }));
    fireEvent.click(screen.getByRole("button", { name: "취소" }));
    expect(on.onKeep).toHaveBeenCalledOnce();
    expect(on.onRelease).toHaveBeenCalledOnce();
    expect(on.onClose).toHaveBeenCalledOnce();
  });
});
