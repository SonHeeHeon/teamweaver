import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { WhatifEmpty } from "./Shell";

describe("상황 화면이 데이터를 바꿔 편성안을 닫으면(사용자 질문 2026-10-21)", () => {
  it("What-if 빈 화면이 이유와 다음 할 일을 보인다", () => {
    render(<WhatifEmpty dataset={null} onCompute={() => {}} onRecords={() => {}} closedBy="신규 사업 편성" />);
    expect(screen.getByRole("note")).toHaveTextContent("신규 사업 편성에서 데이터를 바꿔 보던 편성안을 닫았습니다.");
    expect(screen.getByRole("note")).toHaveTextContent("편성 기록을 다시 열면 기록 당시 데이터로 돌아가");
  });
});
