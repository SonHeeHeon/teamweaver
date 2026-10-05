import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { EvidenceList } from "./EvidenceList";
import { hasMarkers } from "./evidenceMarkers";

const EV = [
  { source_id: "rv:p1>p2#1:pos", reviewer_id: "p1", kind: "quote" as const, text: "꼼꼼하게 문서를 남긴다" },
  { source_id: "rv:p3>p2#2:neg", reviewer_id: "p3", kind: "summary" as const, text: "일정 공유가 늦었다" },
  { source_id: "rv:p4>p2#1:pos", reviewer_id: "p4", kind: "label" as const, text: "문서화" },
];

describe("EvidenceList", () => {
  it("종류마다 배지를 달고, 직접 인용만 따옴표로 감싼다", () => {
    const { container } = render(<EvidenceList evidence={EV} nameOf={(id) => `이름-${id}`} />);
    expect(screen.getByText("직접 인용")).toBeInTheDocument();
    expect(screen.getByText("요약")).toBeInTheDocument();
    expect(screen.getByText("원문 비공개(실데이터)")).toBeInTheDocument();
    const quotes = container.querySelectorAll("q");
    expect(quotes).toHaveLength(1);
    expect(quotes[0]).toHaveTextContent("꼼꼼하게 문서를 남긴다");
    expect(screen.getByText(/이름-p1 리뷰 · rv:p1>p2#1:pos/)).toBeInTheDocument();
  });

  it("본문에 출처 표시가 있으면 그 뜻을 안내한다", () => {
    render(<EvidenceList evidence={EV.slice(0, 1)} hasInlineMarkers />);
    expect(screen.getByText(/문장 전체가 원문으로 검증됐다는 뜻은 아니다/)).toBeInTheDocument();
  });

  it("근거도 출처 표시도 없으면 아무것도 그리지 않는다", () => {
    const { container } = render(<EvidenceList evidence={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("hasMarkers는 [rv:…]만 찾는다", () => {
    expect(hasMarkers(["좋다 [rv:p1>p2#1:pos]"])).toBe(true);
    expect(hasMarkers(["좋다 [참고]", "rv:p1 없음"])).toBe(false);
  });
});
