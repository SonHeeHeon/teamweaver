import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";
import { StaticNetwork } from "./StaticNetwork";

describe("StaticNetwork", () => {
  it("노드와 엣지를 SVG로 그린다 (벡터, 애니메이션 없음)", () => {
    const { container } = render(
      <StaticNetwork
        nodes={[{ id: "a", name: "김" }, { id: "b", name: "이" }]}
        links={[{ source: "a", target: "b", co_months: 9 }]}
      />,
    );
    expect(container.querySelectorAll("circle")).toHaveLength(2);
    expect(container.querySelectorAll("line")).toHaveLength(1);
  });

  it("좌표가 확정돼 있다 (NaN이 남으면 인쇄가 빈다)", () => {
    const { container } = render(
      <StaticNetwork nodes={[{ id: "a", name: "김" }]} links={[]} />,
    );
    const c = container.querySelector("circle")!;
    expect(Number.isFinite(Number(c.getAttribute("cx")))).toBe(true);
    expect(Number.isFinite(Number(c.getAttribute("cy")))).toBe(true);
  });
});

describe("StaticNetwork — 잘림 방지", () => {
  /** 노드가 많으면 force 레이아웃이 고정 뷰포트(560x380)를 넘어 퍼지고,
   *  SVG는 기본적으로 뷰포트 밖을 잘라낸다. 실측: 78노드에서 좌우 노드와
   *  라벨이 잘렸다. viewBox를 실제 좌표 범위에 맞춰야 전부 들어온다. */
  const many = Array.from({ length: 40 }, (_, i) => ({ id: `p${i}`, name: `n${i}` }));
  const chain = many.slice(1).map((n, i) => ({
    source: many[i].id, target: n.id, co_months: 3,
  }));

  it("모든 노드가 viewBox 안에 들어온다", () => {
    const { container } = render(<StaticNetwork nodes={many} links={chain} />);
    const svg = container.querySelector("svg")!;
    const vb = svg.getAttribute("viewBox");
    expect(vb).not.toBeNull();
    const [vx, vy, vw, vh] = vb!.split(/\s+/).map(Number);
    expect(vw).toBeGreaterThan(0);
    expect(vh).toBeGreaterThan(0);

    const circles = [...container.querySelectorAll("circle")];
    expect(circles).toHaveLength(40);
    for (const c of circles) {
      const cx = Number(c.getAttribute("cx"));
      const cy = Number(c.getAttribute("cy"));
      expect(cx).toBeGreaterThanOrEqual(vx);
      expect(cx).toBeLessThanOrEqual(vx + vw);
      expect(cy).toBeGreaterThanOrEqual(vy);
      expect(cy).toBeLessThanOrEqual(vy + vh);
    }
  });
});
