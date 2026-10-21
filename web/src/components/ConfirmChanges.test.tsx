import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

vi.mock("../scenarios/workspaceApi", async (importOriginal) => {
  const real = await importOriginal<typeof import("../scenarios/workspaceApi")>();
  return { ...real, postPreview: vi.fn(), postApply: vi.fn() };
});
import * as api from "../scenarios/workspaceApi";
import { ConfirmChanges } from "./ConfirmChanges";

const ENTRIES = [{ person_id: "p1", project_id: "J1", alloc: 1 }];
const PV = { dataset_version: "v1", moved: [{ person_id: "p1", name: "가", from: ["J2"], to: ["J1"] }], joined: [], left: [], changed: true,
             grade_swaps: {}, budget_add: {}, new_violations: [], locked_dropped: [] };
const props = (over = {}) => ({ datasetVersion: "v1", entries: ENTRIES, source: "plan" as const, title: "안 A 확정", lead: "이 안이 지금 배치가 됩니다.",
  note: "연간 계획 확정: 안 A", onDone: vi.fn(), onClose: vi.fn(), onStale: vi.fn(), ...over });

beforeEach(() => vi.clearAllMocks());

describe("확정 창(연간 계획 확정·What-if 확정 공용, 2026-10-21)", () => {
  it("지금 대비 옮김을 보이고, 확정하면 미리 계산 그대로 보낸다", async () => {
    vi.mocked(api.postPreview).mockResolvedValue(PV);
    vi.mocked(api.postApply).mockResolvedValue({ workspace: { dataset_version: "v2" }, changes: { added: [], removed: [], changed: [] } } as never);
    const p = props();
    render(<ConfirmChanges {...p} />);
    expect(await screen.findByText("지금 배치 대비: 옮김 1명 · 새로 0명 · 빠짐 0명")).toBeInTheDocument();
    expect(screen.getByText("가: J2 → J1")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "확정" }));
    await waitFor(() => expect(p.onDone).toHaveBeenCalled());
    expect(vi.mocked(api.postApply).mock.calls[0]).toEqual(["v1", { entries: ENTRIES, source: "plan", note: "연간 계획 확정: 안 A" }]);
    expect(vi.mocked(p.onDone).mock.calls[0][1]).toBe("v2");
  });

  it("등급 대체는 사유가 있어야, 위반·고정 충돌은 확정할 수 없다", async () => {
    vi.mocked(api.postPreview).mockResolvedValueOnce({ ...PV, grade_swaps: { J1: [{ seat: "중급", grade: "고급", count: 1 }] }, budget_add: { J1: 300 } });
    vi.mocked(api.postApply).mockResolvedValue({ workspace: { dataset_version: "v2" }, changes: { added: [], removed: [], changed: [] } } as never);
    const { unmount } = render(<ConfirmChanges {...props()} />);
    await screen.findByLabelText("J1 중급 자리 고급 대체 사유");
    expect(screen.getByRole("button", { name: "확정" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("J1 중급 자리 고급 대체 사유"), { target: { value: "중급 없음" } });
    fireEvent.click(screen.getByRole("button", { name: "확정" }));
    await waitFor(() => expect(api.postApply).toHaveBeenCalled());
    expect(vi.mocked(api.postApply).mock.calls[0][1]).toMatchObject({
      grade_swaps: [{ project_id: "J1", seat: "중급", grade: "고급", count: 1, reason: "중급 없음" }], budget_add: { J1: 300 } });
    unmount();
    vi.mocked(api.postPreview).mockResolvedValueOnce({ ...PV, new_violations: ["J1 월 비용이 예산을 초과"], locked_dropped: ["나@J3"] });
    render(<ConfirmChanges {...props()} />);
    expect(await screen.findByText(/고정한 배치를 빼는 안이라/)).toBeInTheDocument();
    expect(screen.getByText("J1 월 비용이 예산을 초과")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "확정" })).toBeDisabled();
  });
});
