import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

vi.mock("../scenarios/workspaceApi", async (importOriginal) => {
  const real = await importOriginal<typeof import("../scenarios/workspaceApi")>();
  return { ...real, fetchWorkspace: vi.fn(), postPreview: vi.fn(), postApply: vi.fn() };
});
vi.mock("../api/client", () => ({ DatasetChangedError: class extends Error {}, postWhatif: vi.fn(), applySwap: vi.fn(), API_BASE: "" }));
import * as ws from "../scenarios/workspaceApi";
import * as client from "../api/client";
import { CurrentWhatif } from "./CurrentWhatif";
import type { Meta, WhatifResponse } from "../api/types";
import type { Workspace } from "../scenarios/workspaceApi";

const META = {
  people: [{ id: "P1", name: "김민준", grade: "고급", skills: {} }, { id: "P2", name: "이서연", grade: "중급", skills: {} },
           { id: "P9", name: "박지훈", grade: "고급", skills: {} }],
  projects: [{ id: "J003", name: "은행 A 데이터", sector: "x", phase: "x", start_month: 0, end_month: 5, grade_headcount: { 고급: 1, 중급: 1 }, monthly_budget: 9000 }],
  skills: [], review_items: [], coworks: [], dataset_version: "v1", horizon_start: "2026-10",
} as unknown as Meta;
const WS = {
  dataset_version: "v1", dataset_id: "org", synthetic: true, editable: true, pipeline: [], bench: [{ person_id: "P9", name: "박지훈", grade: "고급" }], history: [],
  ongoing: [{ project_id: "J003", name: "은행 A 데이터", start: "2026-10", end: "2027-03", monthly_budget: 9000, ends_in_horizon: false,
              locked_all: false, locked_count: 0, seats: {},
              team: [{ person_id: "P1", name: "김민준", grade: "고급", alloc: 1, locked: false },
                     { person_id: "P2", name: "이서연", grade: "중급", alloc: 1, locked: false }] }],
} as unknown as Workspace;
const T = { skill: 0, synergy: 0, overfamiliarity: 0, unfilled: 0, total: 0 };
const RESULT = { objective_delta: 1.5, before: T, after: { ...T, skill: 1.5, total: 1.5 }, new_violations: [], new_shortfalls: [], feasible: true,
                 briefing: { rationale: "박지훈이 기술이 더 맞다", risks: [], alternatives: [] }, fallback_used: false } as unknown as WhatifResponse;

beforeEach(() => vi.clearAllMocks());

describe("What-if — 지금 배치에서 바꿔 보기(2026-10-21 사용자 \"현재 프로젝트 인력 배치에서 부서장이 이렇게 바꿔봤을때\")", () => {
  it("지금 배치에서 교체를 보고 담으면 바꾼 뒤 명단이 되고, 담은 변경을 확정 창으로 넘긴다", async () => {
    vi.mocked(ws.fetchWorkspace).mockResolvedValue(WS);
    vi.mocked(client.postWhatif).mockResolvedValue(RESULT);
    const after = [{ person_id: "P9", project_id: "J003", alloc: 1 }, { person_id: "P2", project_id: "J003", alloc: 1 }];
    vi.mocked(client.applySwap).mockResolvedValue({ entries: after, objective_delta: 1.5, feasible: true, warnings: [] } as never);
    vi.mocked(ws.postPreview).mockResolvedValue({ dataset_version: "v1", moved: [], joined: [{ person_id: "P9", name: "박지훈", from: [], to: ["J003"] }],
      left: [{ person_id: "P1", name: "김민준", from: ["J003"], to: [] }], changed: true, grade_swaps: {}, budget_add: {}, new_violations: [], locked_dropped: [] });
    render(<CurrentWhatif active datasetVersion="v1" meta={META} settings={null} onDatasetChanged={vi.fn()} />);
    expect(await screen.findByText("담은 변경 0건")).toBeInTheDocument();
    // 교체 부품의 안내는 What-if의 길(담기 → 확정)을 말한다 -- 편성안 다듬기의 '적용'이 아니다
    expect(screen.getByText(/'이 변경 담기'로 모아 두고, 확정해야 지금 배치가 바뀝니다/)).toBeInTheDocument();
    expect(screen.queryByText(/'적용'해야 바뀝니다/)).toBeNull();
    fireEvent.change(screen.getByLabelText("교체 대상"), { target: { value: "P1::J003" } });
    fireEvent.change(screen.getByLabelText("교체 투입"), { target: { value: "P9" } });
    fireEvent.click(screen.getByRole("button", { name: /교체 효과 보기/ }));
    await waitFor(() => expect(client.postWhatif).toHaveBeenCalled());
    const [entries, swap, weights] = vi.mocked(client.postWhatif).mock.calls[0];
    expect(entries).toEqual([{ person_id: "P1", project_id: "J003", alloc: 1 }, { person_id: "P2", project_id: "J003", alloc: 1 }]);   // 지금 배치 그대로
    expect(swap).toEqual({ out_person_id: "P1", in_person_id: "P9", project_id: "J003" });
    expect(weights).toEqual({});                                                 // 기본 가중치(확정·검토 자료와 같은 기준)
    expect(vi.mocked(client.postWhatif).mock.calls[0][5]).toEqual({ gradeSwap: true });   // 확정과 같은 등급 대체로 평가
    fireEvent.click(await screen.findByRole("button", { name: /이 변경 담기/ }));
    expect(await screen.findByText("담은 변경 1건")).toBeInTheDocument();
    expect(screen.getByRole("list", { name: "담은 변경" })).toHaveTextContent("J003: 김민준 → 박지훈");
    expect(screen.getByText("새로")).toBeInTheDocument();                         // 바꾼 뒤 명단: 박지훈 "새로"
    expect(screen.getByText(/빠짐\(지금 배치 대비\): 김민준/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "1건 확정" }));
    await waitFor(() => expect(ws.postPreview).toHaveBeenCalledWith("v1", after, expect.anything()));
    expect(await screen.findByText("지금 배치 대비: 옮김 0명 · 새로 1명 · 빠짐 1명")).toBeInTheDocument();
  });

  it("지금 배치가 없으면(백지) 연간 계획 확정이나 운영 중 기록을 안내한다", async () => {
    vi.mocked(ws.fetchWorkspace).mockResolvedValue({ ...WS, ongoing: [], bench: [] } as unknown as Workspace);
    render(<CurrentWhatif active datasetVersion="v1" meta={META} settings={null} onDatasetChanged={vi.fn()} />);
    expect(await screen.findByText("지금 배치가 없습니다")).toBeInTheDocument();
    expect(screen.getByText(/연간 계획에서 안을 확정하거나/)).toBeInTheDocument();
  });
});
