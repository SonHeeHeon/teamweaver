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
    render(<ConfirmChanges {...props({ source: "manual" as const })} />);
    expect(await screen.findByText(/고정한 배치를 빼는 안이라/)).toBeInTheDocument();
    expect(screen.getByText("J1 월 비용이 예산을 초과")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "확정" })).toBeDisabled();
  });

  it("연간 계획 안의 고정 충돌은 '고정을 풀고 확정'으로 넘긴다(풀고 짠 안이면 미리 체크)", async () => {
    vi.mocked(api.postPreview).mockResolvedValue({ ...PV, locked_dropped: ["나@J3"] });
    vi.mocked(api.postApply).mockResolvedValue({ workspace: { dataset_version: "v2" }, changes: { added: [], removed: [], changed: [] } } as never);
    const { unmount } = render(<ConfirmChanges {...props({ keepLocked: false })} />);
    expect(await screen.findByText(/고정을 풀고 다시 배치한 안입니다/)).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "고정을 풀고 확정합니다" })).toBeChecked();
    fireEvent.click(screen.getByRole("button", { name: "확정" }));
    await waitFor(() => expect(api.postApply).toHaveBeenCalled());
    expect(vi.mocked(api.postApply).mock.calls[0][1]).toMatchObject({ release_locks: true });
    unmount();
    render(<ConfirmChanges {...props()} />);                       // 묻지 않은 안(편성 기록 등): 직접 체크해야 확정
    const box = await screen.findByRole("checkbox", { name: "고정을 풀고 확정합니다" });
    expect(box).not.toBeChecked();
    expect(screen.getByRole("button", { name: "확정" })).toBeDisabled();
    fireEvent.click(box);
    expect(screen.getByRole("button", { name: "확정" })).toBeEnabled();
  });

  it("빼 온 자리는 메울 사람을 넣거나 '메우지 않음'과 사유를 적어야 확정한다(2026-10-21)", async () => {
    const holes = { J2: [{ grade: "중급" as const, count: 1 }] };
    const opt = { person_id: "p9", name: "다", grade: "중급" as const, alloc: 1, skill_fit: 0.5, delta_total: 0.4 };
    vi.mocked(api.postPreview).mockResolvedValueOnce({ ...PV, holes, fill_options: { J2: [opt] }, fills: [] })
      .mockResolvedValueOnce({ ...PV, holes: {}, fill_options: {},
                               fills: [{ project_id: "J2", person_id: "p9", name: "다", grade: "중급", from_bench: true }] });
    vi.mocked(api.postApply).mockResolvedValue({ workspace: { dataset_version: "v2" }, changes: { added: [], removed: [], changed: [] } } as never);
    const { unmount } = render(<ConfirmChanges {...props({ source: "manual" as const })} />);
    expect(await screen.findByText(/빼 온 자리 1개가 빕니다/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "확정" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: /다 넣기/ }));               // 메움 -- 그 사람을 넣어 다시 미리 계산
    expect(await screen.findByText(/빼 온 자리를 메운 사람/)).toBeInTheDocument();
    expect(vi.mocked(api.postPreview).mock.calls[1][1]).toEqual([...ENTRIES, { person_id: "p9", project_id: "J2", alloc: 1 }]);
    fireEvent.click(screen.getByRole("button", { name: "확정" }));
    await waitFor(() => expect(api.postApply).toHaveBeenCalled());
    expect(vi.mocked(api.postApply).mock.calls[0][1]).toMatchObject({ entries: [...ENTRIES, { person_id: "p9", project_id: "J2", alloc: 1 }] });
    expect(vi.mocked(api.postApply).mock.calls[0][1]).not.toHaveProperty("seat_waivers");
    unmount();

    vi.clearAllMocks();
    vi.mocked(api.postPreview).mockResolvedValue({ ...PV, holes, fill_options: { J2: [] }, fills: [] });
    vi.mocked(api.postApply).mockResolvedValue({ workspace: { dataset_version: "v2" }, changes: { added: [], removed: [], changed: [] } } as never);
    render(<ConfirmChanges {...props({ source: "manual" as const })} />);
    expect(await screen.findByText("지금 바로 넣을 수 있는 사람이 없습니다.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("checkbox", { name: "메우지 않음" }));
    expect(screen.getByRole("button", { name: "확정" })).toBeDisabled();            // 사유가 비면 안 된다
    fireEvent.change(screen.getByLabelText("J2 중급 자리 메우지 않는 사유"), { target: { value: "범위 축소" } });
    fireEvent.click(screen.getByRole("button", { name: "확정" }));
    await waitFor(() => expect(api.postApply).toHaveBeenCalled());
    expect(vi.mocked(api.postApply).mock.calls[0][1]).toMatchObject({
      seat_waivers: [{ project_id: "J2", grade: "중급", count: 1, reason: "범위 축소" }] });
  });
});
