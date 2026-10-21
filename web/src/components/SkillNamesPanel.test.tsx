import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

vi.mock("../scenarios/workspaceApi", async (importOriginal) => {
  const real = await importOriginal<typeof import("../scenarios/workspaceApi")>();
  return { ...real, fetchUnknownSkills: vi.fn(), postSkillSuggest: vi.fn(), postSkillNames: vi.fn() };
});
import * as api from "../scenarios/workspaceApi";
import { SkillNamesPanel } from "./SkillNamesPanel";

const K = { name: "Kuberentes", people: 15, projects: [], works: 0, co_skills: ["Docker"], candidates: [{ id: "kubernetes", label: "Kubernetes", score: 0.9 }] };
const J = { name: "자바스크립", people: 3, projects: [], works: 0, co_skills: [], candidates: [{ id: "javascript", label: "JavaScript", score: 0.93 },
                                                                                            { id: "java", label: "Java", score: 0.57 }] };

beforeEach(() => vi.clearAllMocks());

describe("기술 이름 정리(2026-10-22)", () => {
  it("AI 제안을 보이고, 확실한 것만 한 번에, 헷갈리는 짝은 표시만 하고 고른 결정을 보낸다", async () => {
    vi.mocked(api.fetchUnknownSkills).mockResolvedValue({ dataset_version: "v1", dictionary_version: "d", items: [K, J], decided: [] });
    vi.mocked(api.postSkillSuggest).mockResolvedValue({ method: "ai", fallback_reason: null, ai_available: true,
      usage: { calls: 1, cost_usd: 0.002, latency_s: 6 }, items: [
        { ...K, decision: "alias", concept: "kubernetes", concept_label: "Kubernetes", alternatives: [], reason: "오타", confidence: "high", flags: [], method: "ai" },
        { ...J, decision: "alias", concept: "javascript", concept_label: "JavaScript", alternatives: [{ id: "java", label: "Java" }], reason: "줄임",
          confidence: "check", flags: ["헷갈리기 쉬운 기술 — JavaScript ↔ Java"], method: "ai" }] });
    vi.mocked(api.postSkillNames).mockResolvedValue({ workspace: { dataset_version: "v2" } } as never);
    const onChanged = vi.fn();
    render(<SkillNamesPanel datasetVersion="v1" onChanged={onChanged} />);
    expect(await screen.findByText("Kuberentes")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "AI 제안 받기" }));
    expect(await screen.findByText(/헷갈리기 쉬운 기술/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "확실한 1개 한 번에 반영" }));
    await waitFor(() => expect(api.postSkillNames).toHaveBeenCalled());
    expect(vi.mocked(api.postSkillNames).mock.calls[0][1]).toEqual([
      { name: "Kuberentes", decision: "alias", concept: "kubernetes", method: "ai", reason: "오타" }]);
    expect(onChanged).toHaveBeenCalled();
  });
});
