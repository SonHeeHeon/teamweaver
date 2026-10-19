import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DataSourcePanel } from "./DataSourcePanel";

const TABLES = [
  { name: "people.csv", from: "api", service: false }, { name: "reviews.csv", from: "data", service: false },
  { name: "projects.csv", from: "data", service: true },
];
const BASE = {
  mode: "api", config_error: null, host: "hr.example", tables: TABLES, last: null, running: false, editable: true,
  not_editable_reason: null, skill_months_owner: "service", as_of: "2026-09",
  aging: { owner: "service", as_of: "2026-09", cutoff: "2026-09", people: 0, skills: 0, max_months: 0 },
};
const INFO = { dataset_id: "d", version: "v2", source: "upload", synthetic: true, people: 100, projects: 20, activated_at: "" };

function mockFetch(handler: (url: string, init?: RequestInit) => Response) {
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => handler(url, init)));
}
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });

afterEach(() => { vi.unstubAllGlobals(); });

describe("데이터 원천(2026-10-19 인사 시스템 API 기본·CSV 대체)", () => {
  it("API 모드: 표별 원천을 보이고, 지금 받기로 새 판이 켜지면 화면을 바꾼다", async () => {
    const switched = vi.fn();
    mockFetch((url) => url.endsWith("/sync")
      ? json({ result: { at: "2026-10-19T00:00:00Z", trigger: "manual", status: "applied", detail: "", changed: ["people.csv"] },
               dataset: INFO, status: { ...BASE, last: { at: "2026-10-19T00:00:00Z", trigger: "manual", status: "applied", detail: "" } } })
      : json(BASE));
    render(<DataSourcePanel datasetVersion="v1" onSwitched={switched} />);
    expect(await screen.findByText(/인사 시스템 API\(hr.example\)/)).toBeInTheDocument();
    expect(screen.getByText("인력")).toBeInTheDocument();
    expect(screen.getByText("동료 평가")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "지금 받기" }));
    await waitFor(() => expect(switched).toHaveBeenCalledWith(INFO));
    expect((await screen.findAllByText("새 판으로 켰다")).length).toBe(2);       // 마지막 받기 줄 + 알림
  });

  it("CSV 모드에는 지금 받기가 없고, 갱신 주체를 바꾸면 판 버전과 함께 보낸다", async () => {
    const bodies: string[] = [];
    const switched = vi.fn();
    mockFetch((url, init) => {
      if (url.endsWith("/skill-months-owner")) {
        bodies.push(init?.body as string);
        return json({ dataset: INFO, status: { ...BASE, mode: "csv", skill_months_owner: "source" } });
      }
      return json({ ...BASE, mode: "csv", host: null });
    });
    render(<DataSourcePanel datasetVersion="v1" onSwitched={switched} />);
    expect(await screen.findByText(/CSV 묶음\(데이터 탭 업로드\)/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "지금 받기" })).toBeNull();
    const apply = screen.getByRole("button", { name: "적용" });
    expect(apply).toBeDisabled();
    fireEvent.click(screen.getByRole("radio", { name: /원천 시스템이 갱신한다/ }));
    fireEvent.click(apply);
    await waitFor(() => expect(switched).toHaveBeenCalledWith(INFO));
    expect(JSON.parse(bodies[0])).toEqual({ dataset_version: "v1", owner: "source" });
  });

  it("저장 실패는 성공처럼 보이지 않는다, 표가 없어 못 늘린 경력은 '최신'과 구분한다", async () => {
    mockFetch((url) => url.endsWith("/sync")
      ? json({ result: { at: "2026-10-19T00:00:00Z", trigger: "manual", status: "applied", detail: "", persisted: false,
                         persist_error: "디스크 가득" }, dataset: INFO, status: BASE })
      : json({ ...BASE, aging: { ...BASE.aging, as_of: "2026-06", skipped: "no_work_skills" } }));
    render(<DataSourcePanel datasetVersion="v1" onSwitched={() => {}} />);
    expect(await screen.findByText(/업무별 사용 기술 표가 없어 늘리지 못함 — 데이터 기준월 2026-06 뒤/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "지금 받기" }));
    expect(await screen.findByText(/서버에 저장하지 못했다.*디스크 가득/)).toBeInTheDocument();
  });

  it("어디까지 셌는지 몰라 못 늘린 경력은 '최신'으로 보이지 않는다", async () => {
    mockFetch(() => json({ ...BASE, aging: { ...BASE.aging, as_of: null, skipped: "unknown_counted_month", held: 3 } }));
    render(<DataSourcePanel datasetVersion="v1" onSwitched={() => {}} />);
    expect(await screen.findByText(/어디까지 셌는지.*경력 3건을 늘리지 못함/)).toBeInTheDocument();
  });

  it("다른 곳에서 받는 중이면 끝날 때까지 다시 읽고, 새 판이 켜졌으면 화면에 알린다", async () => {
    let n = 0;
    const changed = vi.fn();
    mockFetch(() => (++n < 3 ? json({ ...BASE, running: true })
      : json({ ...BASE, running: false, last: { at: "2026-10-19T00:00:00Z", trigger: "nightly", status: "applied", detail: "", version: "v9" } })));
    render(<DataSourcePanel datasetVersion="v1" onSwitched={() => {}} onExternalChange={changed} pollMs={5} />);
    expect(await screen.findByRole("button", { name: "받는 중…" })).toBeDisabled();
    await waitFor(() => expect(changed).toHaveBeenCalled());
    expect(await screen.findByRole("button", { name: "지금 받기" })).toBeEnabled();
  });

  it("고칠 수 없는 데이터면 이유를 보이고 적용을 막는다", async () => {
    mockFetch(() => json({ ...BASE, editable: false, not_editable_reason: "기본 데이터는 고칠 수 없다" }));
    render(<DataSourcePanel datasetVersion="v1" onSwitched={() => {}} />);
    expect(await screen.findByText("기본 데이터는 고칠 수 없다")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "적용" })).toBeDisabled();
  });
});
