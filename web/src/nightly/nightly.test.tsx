import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { NightlyBanner } from "./NightlyBanner";
import { NightlyPanel } from "./NightlyPanel";

const BASE = { enabled: true, window: "00:00-05:00", next_run_at: "2026-10-20T00:00", running: false, progress: null };

function mockFetch(handler: (url: string, init?: RequestInit) => Response) {
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => handler(url, init)));
}
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });

afterEach(() => { vi.unstubAllGlobals(); });

describe("야간 배치 화면(claude-b 요청 (1)(2))", () => {
  it("아침 알림: 확인할 것이 있으면 펼쳐 본다", async () => {
    mockFetch(() => json({ ...BASE, last: { status: "attention", summary: "확인 필요 1건 · 긴급 대기 명단 12개 사업",
                                          attention: [{ title: "운영 비교", detail: "끝까지 풀지 못함" }] } }));
    render(<NightlyBanner />);
    expect(await screen.findByText(/확인 필요 1건/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "확인할 것 1건 보기" }));
    expect(screen.getByText(/끝까지 풀지 못함/)).toBeInTheDocument();
  });

  it("돌고 있으면 진행 중, 야간 배치가 없는 서버면 아무것도 안 보인다", async () => {
    mockFetch(() => json({ ...BASE, running: true, progress: { phase: "긴급 대기 명단" }, last: null }));
    const { unmount } = render(<NightlyBanner />);
    expect(await screen.findByText(/밤 배치 진행 중 — 긴급 대기 명단/)).toBeInTheDocument();
    unmount();
    mockFetch(() => json({ detail: "없음" }, 503));
    const { container } = render(<NightlyBanner />);
    await waitFor(() => expect(container).toBeEmptyDOMElement());
  });

  it("관리자: 지금 한 번 돌리기는 분을 보내고, 이미 돌고 있으면 알린다", async () => {
    const calls: [string, string | undefined][] = [];
    mockFetch((url, init) => {
      calls.push([url, init?.body as string | undefined]);
      if (url.endsWith("/api/nightly/run")) return json({ detail: "이미" }, 409);
      return json({ ...BASE, last: null });
    });
    render(<NightlyPanel />);
    fireEvent.change(await screen.findByLabelText("밤 배치 최대 시간(분)"), { target: { value: "15" } });
    fireEvent.click(screen.getByRole("button", { name: "지금 한 번 돌리기" }));
    expect(await screen.findByText("이미 돌고 있습니다.")).toBeInTheDocument();
    expect(calls.find(([u]) => u.endsWith("/api/nightly/run"))?.[1]).toBe(JSON.stringify({ minutes: 15 }));
  });
});
