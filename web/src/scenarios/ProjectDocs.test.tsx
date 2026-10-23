import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

vi.mock("./workspaceApi", async (importOriginal) => {
  const real = await importOriginal<typeof import("./workspaceApi")>();
  return { ...real, fetchWorkspace: vi.fn(), fetchCandidates: vi.fn(), fetchSkillDictionary: vi.fn(), postProject: vi.fn() };
});
vi.mock("./projectDocsApi", async (importOriginal) => {
  const real = await importOriginal<typeof import("./projectDocsApi")>();
  const uploadProjectDoc = vi.fn();
  // uploadAll은 모듈 안의 진짜 올리기를 부르므로 흉내 낸 올리기로 다시 묶는다(진짜 uploadAll은 아래 시험에서 따로 본다)
  const uploadAll = vi.fn(async (v: string, pid: string, docs: { file: File; kind: string }[]) => {
    for (const d of docs) await uploadProjectDoc(v, pid, d.kind, d.file);
    return { ok: docs.length, failed: [] };
  });
  return { ...real, fetchProjectDocs: vi.fn(), fetchRequirements: vi.fn(), uploadProjectDoc, uploadAll, deleteProjectDoc: vi.fn(),
           fetchDocSummary: vi.fn(), makeDocSummary: vi.fn() };
});
vi.mock("../api/client", () => ({
  DatasetChangedError: class extends Error {}, streamOperatingCompare: vi.fn(), postJustification: vi.fn(), API_BASE: "",
}));

import * as api from "./workspaceApi";
import * as docsApi from "./projectDocsApi";
import { DocLinks, DocPicker, DocsDialog, RequirementLine } from "./ProjectDocs";
import { ProjectForm } from "./ProjectForm";
import { NewProjectsScreen } from "./NewProjectsScreen";
import type { Workspace } from "./workspaceApi";
import type { ProjectDoc } from "./projectDocsApi";

const doc = (over: Partial<ProjectDoc>): ProjectDoc => ({
  id: "d1", project_id: "J014", kind: "제안요청서(RFP)", filename: "RFP.pdf", size: 140_000, uploaded_at: null, demo: false, ...over,
});
const WS: Workspace = {
  dataset_version: "v1", dataset_id: "org", synthetic: true, editable: true, horizon_start: "2026-10", horizon_last: "2027-03",
  pipeline: [{ project_id: "J014", name: "보험사 B 고도화", start: "2026-11", end: "2027-03", monthly_budget: 6000, stage: "제안",
               predecessor: null, seats: { 고급: { need: 2, filled: 0 } }, team: [] }],
  ongoing: [{ project_id: "J003", name: "은행 A 데이터", start: "2026-10", end: "2026-12", monthly_budget: 9000, ends_in_horizon: true,
              locked_all: false, locked_count: 0, seats: {}, team: [{ person_id: "P1", name: "김민준", grade: "고급", alloc: 1, locked: false }] }],
  bench: [], history: [],
};
const pdf = (name = "사전규격.pdf", size = 2000) => new File([new Uint8Array(size)], name, { type: "application/pdf" });

beforeEach(() => { vi.clearAllMocks(); });

describe("발주 자료 · 요구 기술 부품(2026-10-23 사용자 \"신규사업 제목 오른쪽에 그 자료를 다운 받을 수 있는 버튼\")", () => {
  it("받기 버튼은 종류 이름이고, 같은 종류가 또 있으면 번호를 붙인다", () => {
    render(<DocLinks docs={[doc({}), doc({ id: "d2", kind: "사전규격", filename: "pre.pdf", demo: true }), doc({ id: "d3", filename: "RFP2.hwp" })]} />);
    const links = screen.getAllByRole("link");
    expect(links.map((a) => a.textContent)).toEqual(["⤓제안요청서(RFP)", "⤓사전규격", "⤓제안요청서(RFP) 2"]);
    expect(links[0]).toHaveAttribute("href", "/api/project-docs/J014/d1");
    expect(links[0]).toHaveAttribute("download", "RFP.pdf");
    expect(links[1].getAttribute("title")).toContain("시연용 가상 자료");
  });

  it("요구 기술은 등록한 이름·경력·레벨·인원이고, 사전 대표 이름이 다르면 괄호로 함께", () => {
    render(<RequirementLine reqs={[{ skill: "ETL", canonical: null, months: 60, level: 4, headcount: 2 },
                                   { skill: "자바", canonical: "Java", months: 18, level: 2, headcount: 1 },
                                   { skill: "SQL", canonical: null, months: null, level: 3, headcount: 1 }]} />);
    const items = within(screen.getByRole("list", { name: "요구 기술" })).getAllByRole("listitem").map((li) => li.textContent);
    expect(items).toEqual(["ETL · 경력 5년 이상 · 레벨 4 · 2명", "자바(Java) · 경력 1년 6개월 이상 · 레벨 2 · 1명", "SQL · 레벨 3 이상 · 1명"]);
  });

  it("양식 칸은 받는 형식만 담고, 기본 종류는 단계에 맞춘다(발주 예정 = 사전규격)", () => {
    const onChange = vi.fn();
    render(<DocPicker value={[]} onChange={onChange} stage="예정" />);
    fireEvent.change(screen.getByLabelText("발주 자료 파일"), { target: { files: [pdf(), new File(["x"], "run.exe")] } });
    expect(onChange).toHaveBeenCalledWith([expect.objectContaining({ kind: "사전규격" })]);
    expect(onChange.mock.calls[0][0][0].file.name).toBe("사전규격.pdf");
    expect(screen.getByRole("alert")).toHaveTextContent("run.exe: 받는 형식은");
  });

  it("양식 칸은 한 번에 10개까지만 담고 넘친 개수를 알린다", () => {
    const onChange = vi.fn();
    const nine = Array.from({ length: 9 }, (_, i) => ({ file: pdf(`a${i}.pdf`), kind: "기타" as const }));
    render(<DocPicker value={nine} onChange={onChange} stage="제안" />);
    fireEvent.change(screen.getByLabelText("발주 자료 파일"), { target: { files: [pdf("b1.pdf"), pdf("b2.pdf"), pdf("b3.pdf")] } });
    expect(onChange.mock.calls[0][0]).toHaveLength(10);
    expect(screen.getByRole("alert")).toHaveTextContent("한 번에 10개까지 올린다 -- 2개는 담지 않았다");
  });

  it("자료 관리 창에서 올리다 데이터 판이 바뀌었으면(409) 새 판을 다시 읽게 한다", async () => {
    vi.mocked(docsApi.uploadAll).mockRejectedValueOnce(new api.ApiError("판이 바뀜", 409, { code: "dataset_changed" }));
    const onStale = vi.fn(), onChanged = vi.fn();
    render(<DocsDialog projectId="J014" projectName="보험사 B" stage="제안" docs={[]} version="v1"
                       onChanged={onChanged} onStale={onStale} onClose={vi.fn()} />);
    fireEvent.change(screen.getByLabelText("발주 자료 파일"), { target: { files: [pdf("RFP.pdf")] } });
    fireEvent.click(screen.getByRole("button", { name: "1개 올리기" }));
    await waitFor(() => expect(onStale).toHaveBeenCalled());
    expect(onChanged).not.toHaveBeenCalled();
  });

  it("등록 양식은 고른 자료를 등록과 함께 넘긴다(사업 번호가 생긴 뒤 올린다)", async () => {
    vi.mocked(api.fetchSkillDictionary).mockResolvedValue([]);
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(<ProjectForm start="2026-10" predecessors={[]} onSubmit={onSubmit} onCancel={vi.fn()} />);
    fireEvent.change(screen.getByLabelText("사업명"), { target: { value: "보험사 C 데이터" } });
    fireEvent.change(screen.getByLabelText("발주 자료 파일"), { target: { files: [pdf("RFP 초안.pdf")] } });
    expect(screen.getByLabelText("RFP 초안.pdf 종류")).toHaveValue("사전규격");              // 기본 단계 = 발주 예정
    fireEvent.change(screen.getByLabelText("RFP 초안.pdf 종류"), { target: { value: "제안요청서(RFP)" } });
    for (let i = 0; i < 3; i++) fireEvent.click(screen.getByRole("button", { name: "다음" }));
    fireEvent.change(screen.getByLabelText("기술 검색"), { target: { value: "SQL" } });
    fireEvent.keyDown(screen.getByLabelText("기술 검색"), { key: "Enter" });
    fireEvent.click(screen.getByRole("button", { name: "다음" }));
    expect(screen.getByText("제안요청서(RFP) RFP 초안.pdf")).toBeInTheDocument();          // 확인 단계
    fireEvent.click(screen.getByRole("button", { name: "등록" }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalled());
    expect(onSubmit.mock.calls[0][1]).toEqual([expect.objectContaining({ kind: "제안요청서(RFP)" })]);
  });
});

describe("신규 사업 편성 화면 — 제목 옆 받기 · 요구 기술 · 진행 사업 받기 · 자료 관리", () => {
  it("신규 사업 제목 오른쪽에 자료 버튼, 그 아래 요구 기술, 진행 사업 줄에도 받기 버튼이 있다", async () => {
    vi.mocked(api.fetchWorkspace).mockResolvedValue(WS);
    vi.mocked(api.fetchCandidates).mockResolvedValue({ project_id: "J014", predecessor: null, note: "", bench: [], ending: [], pull: [] } as never);
    vi.mocked(docsApi.fetchProjectDocs).mockResolvedValue({ dataset_version: "v1", max_per_project: 10, docs: {
      J014: [doc({})], J003: [doc({ id: "s1", project_id: "J003", kind: "과업지시서", filename: "SOW.pdf", demo: true })] } });
    vi.mocked(docsApi.fetchRequirements).mockResolvedValue({ dataset_version: "v1", requirements: {
      J014: [{ skill: "ETL", canonical: null, months: 24, level: 2, headcount: 2 }], J003: [] } });
    vi.mocked(docsApi.deleteProjectDoc).mockResolvedValue({ deleted: "d1" });
    render(<NewProjectsScreen milpParams={null} datasetVersion="v1" onDatasetChanged={vi.fn()} />);
    const heading = await screen.findByRole("heading", { name: /보험사 B 고도화/ });
    expect(await within(heading).findByRole("link", { name: /발주 자료 제안요청서\(RFP\) 내려받기/ })).toHaveAttribute("href", "/api/project-docs/J014/d1");
    expect(await screen.findByText("ETL")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /발주 자료 과업지시서 내려받기\(SOW\.pdf\)/ })).toHaveAttribute("href", "/api/project-docs/J003/s1");
    // 자료 관리: 지우면 목록을 다시 읽는다
    fireEvent.click(within(heading).getByRole("button", { name: "자료 관리" }));
    const dialog = screen.getByRole("dialog", { name: /발주 자료 — 보험사 B 고도화/ });
    fireEvent.click(within(dialog).getByRole("button", { name: "RFP.pdf 지우기" }));
    await waitFor(() => expect(docsApi.deleteProjectDoc).toHaveBeenCalledWith("v1", "J014", "d1"));
    await waitFor(() => expect(docsApi.fetchProjectDocs).toHaveBeenCalledTimes(2));
  });

  it("자료가 있으면 사업명·\"자료 요약\"을 누르면 자료 요약 창이 열린다(2026-10-23 사용자 \"등록한 프로젝트명을 누르거나 뭐 어디서든\")", async () => {
    vi.mocked(api.fetchWorkspace).mockResolvedValue(WS);
    vi.mocked(api.fetchCandidates).mockResolvedValue({ project_id: "J014", predecessor: null, note: "", bench: [], ending: [], pull: [] } as never);
    vi.mocked(docsApi.fetchProjectDocs).mockResolvedValue({ dataset_version: "v1", max_per_project: 10, docs: {
      J014: [doc({})], J003: [doc({ id: "s1", project_id: "J003", kind: "과업지시서", filename: "SOW.pdf", demo: true })] } });
    vi.mocked(docsApi.fetchRequirements).mockResolvedValue({ dataset_version: "v1", requirements: { J014: [], J003: [] } });
    vi.mocked(docsApi.fetchDocSummary).mockResolvedValue({ doc: doc({}), readable: true, summary: null, created_at: null, ai_destination: "external" });
    render(<NewProjectsScreen milpParams={null} datasetVersion="v1" onDatasetChanged={vi.fn()} />);
    const heading = await screen.findByRole("heading", { name: /보험사 B 고도화/ });
    fireEvent.click(await within(heading).findByRole("button", { name: "보험사 B 고도화" }));
    expect(screen.getByRole("dialog", { name: "자료 요약 — 보험사 B 고도화" })).toBeInTheDocument();
    await waitFor(() => expect(docsApi.fetchDocSummary).toHaveBeenCalledWith("J014", "d1"));
    fireEvent.click(screen.getByRole("button", { name: "닫기" }));
    fireEvent.click(screen.getByRole("button", { name: "은행 A 데이터 자료 요약" }));
    expect(screen.getByRole("dialog", { name: "자료 요약 — 은행 A 데이터" })).toBeInTheDocument();
  });

  it("등록할 때 고른 자료는 새 사업 번호로 올리고 결과를 알린다", async () => {
    const after = { ...WS, dataset_version: "v2", pipeline: [...WS.pipeline, { ...WS.pipeline[0], project_id: "J021", name: "보험사 C 데이터" }] };
    vi.mocked(api.fetchWorkspace).mockResolvedValue(WS);
    vi.mocked(api.fetchCandidates).mockResolvedValue({ project_id: "J014", predecessor: null, note: "", bench: [], ending: [], pull: [] } as never);
    vi.mocked(api.fetchSkillDictionary).mockResolvedValue([]);
    vi.mocked(api.postProject).mockResolvedValue({ workspace: after, project_id: "J021" });
    vi.mocked(docsApi.fetchProjectDocs).mockResolvedValue({ dataset_version: "v1", max_per_project: 10, docs: {} });
    vi.mocked(docsApi.fetchRequirements).mockResolvedValue({ dataset_version: "v1", requirements: {} });
    vi.mocked(docsApi.uploadProjectDoc).mockResolvedValue({ doc: doc({ project_id: "J021" }) });
    render(<NewProjectsScreen milpParams={null} datasetVersion="v1" onDatasetChanged={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: "신규 사업 등록" }));
    fireEvent.change(screen.getByLabelText("사업명"), { target: { value: "보험사 C 데이터" } });
    fireEvent.change(screen.getByLabelText("발주 자료 파일"), { target: { files: [pdf("RFP.pdf")] } });
    for (let i = 0; i < 3; i++) fireEvent.click(screen.getByRole("button", { name: "다음" }));
    fireEvent.change(screen.getByLabelText("기술 검색"), { target: { value: "SQL" } });
    fireEvent.keyDown(screen.getByLabelText("기술 검색"), { key: "Enter" });
    fireEvent.click(screen.getByRole("button", { name: "다음" }));
    fireEvent.click(screen.getByRole("button", { name: "등록" }));
    await waitFor(() => expect(docsApi.uploadProjectDoc).toHaveBeenCalledWith("v2", "J021", "사전규격", expect.any(File)));
    expect(await screen.findByText(/발주 자료 1개를 올렸다/)).toBeInTheDocument();
  });
});

describe("uploadAll — 등록은 이미 끝났으므로 실패한 자료만 이유와 함께 돌려준다", () => {
  it("하나가 거절돼도 나머지는 올린다", async () => {
    const real = await vi.importActual<typeof import("./projectDocsApi")>("./projectDocsApi");
    const fetchMock = vi.fn(async (url: string) => url.includes("bad.pdf")
      ? new Response(JSON.stringify({ detail: "파일 내용이 PDF 형식이 아니다" }), { status: 422 })
      : new Response(JSON.stringify({ doc: doc({}) }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    try {
      const out = await real.uploadAll("v1", "J014", [{ file: pdf("ok.pdf"), kind: "사전규격" }, { file: pdf("bad.pdf"), kind: "기타" }]);
      expect(out).toEqual({ ok: 1, failed: ["bad.pdf(파일 내용이 PDF 형식이 아니다)"] });
      // 판이 바뀐 거절(409 dataset_changed)은 삼키지 않는다 -- 같은 옛 판으로 계속 실패하지 않게
      fetchMock.mockImplementationOnce(async () => new Response(JSON.stringify({ detail: { code: "dataset_changed", message: "x" } }), { status: 409 }));
      await expect(real.uploadAll("v0", "J014", [{ file: pdf("ok.pdf"), kind: "기타" }])).rejects.toMatchObject({ status: 409 });
      expect(fetchMock.mock.calls[0][0]).toBe(`/api/project-docs/J014?dataset_version=v1&kind=${encodeURIComponent("사전규격")}&filename=ok.pdf`);
    } finally {
      vi.unstubAllGlobals();
    }
  });
});
