import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

vi.mock("../api/client", () => ({ DatasetChangedError: class extends Error {}, fetchSkillMap: vi.fn() }));
import { fetchSkillMap } from "../api/client";
import { SkillMapTab } from "./SkillMapTab";
import type { SkillMap, SkillMapRow } from "../api/types";

const row = (over: Partial<SkillMapRow>): SkillMapRow => ({
  skill: "Java", category: "언어", holders: 5, practical: 3, expert: 1, practical_free: 1, practical_credited: 1,
  demand_projects: 1, demand_headcount: 2, proposal_headcount: 2, proposal_gap: 1, coverage: 1.5, scarce: false,
  demand: [{ project_id: "J1", project: "신규 포털", proposal: true, headcount: 2, min_months: 36, qualified_people: 2 }],
  practical_people: [{ id: "DP1", name: "김민준", credited: false }, { id: "DP2", name: null, credited: true },
                     { id: "DP3", name: null, credited: false }], ...over,
});
const MAP: SkillMap = {
  dataset_version: "v1", note: "계산상 지표 NOT_CALIBRATED.",
  summary: { skills: 3, scarce: 1, proposal_gap_skills: 1, demanded: 2 },
  rows: [row({}), row({ skill: "Rust", scarce: true, practical: 1, practical_credited: 0, proposal_gap: 0, practical_people: [] }),
         row({ skill: "COBOL", demand_headcount: 0, demand_projects: 0, proposal_gap: 0, demand: [] })],
};

describe("SkillMapTab", () => {
  it("요구 있는 기술을 먼저 보이고, 필터·자세히로 수요 사업과 인정받은 사람을 구분해 보인다", async () => {
    vi.mocked(fetchSkillMap).mockResolvedValue(MAP);
    render(<SkillMapTab datasetVersion="v1" onDatasetChanged={vi.fn()} />);
    expect(await screen.findByText("Java")).toBeInTheDocument();
    expect(screen.queryByText("COBOL")).not.toBeInTheDocument();            // 기본: 요구 있는 기술만
    fireEvent.click(screen.getByLabelText("희소(2명 이하)"));
    expect(screen.getByText("Rust")).toBeInTheDocument();
    expect(screen.queryByText("Java")).not.toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("전체"));
    fireEvent.click(screen.getByRole("button", { name: "Java 자세히" }));
    expect(screen.getByText(/신규 포털\(J1\) · 2명 · 레벨 3 이상/)).toBeInTheDocument();
    expect(screen.getByText("김민준(DP1)")).toBeInTheDocument();
    expect(screen.getByText("DP2 · 인정")).toBeInTheDocument();
    expect(vi.mocked(fetchSkillMap).mock.calls[0][0]).toBe("v1");          // 화면이 본 데이터 버전을 보낸다
  });

  it("묶음 데이터가 아니면 서버 이유를 보인다", async () => {
    vi.mocked(fetchSkillMap).mockRejectedValue(new Error("조직 기술 지도는 CSV 묶음 데이터에서만"));
    render(<SkillMapTab datasetVersion="v1" onDatasetChanged={vi.fn()} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("CSV 묶음 데이터에서만");
  });
});
