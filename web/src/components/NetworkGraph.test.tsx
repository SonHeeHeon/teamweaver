import { describe, expect, it, vi } from "vitest";
import { render } from "@testing-library/react";

const seen: any[] = [];
vi.mock("react-force-graph-2d", () => ({
  default: (props: any) => {
    seen.push(props);
    return <div data-testid="fg" />;
  },
}));

import { NetworkGraph } from "./NetworkGraph";
import type { Person, Cowork, AssignEntry } from "../api/types";

const people: Person[] = [
  { id: "p000", name: "김", grade: "중급", skills: {} },
  { id: "p001", name: "이", grade: "고급", skills: {} },
  { id: "p002", name: "박", grade: "초급", skills: {} },
];
const coworks: Cowork[] = [{ a_id: "p000", b_id: "p001", co_months: 9, project_count: 0 }];
const entries: AssignEntry[] = [{ person_id: "p000", project_id: "j00", alloc: 1 }];

describe("NetworkGraph", () => {
  it("배치된 인력만 노드로 올리고 그들 사이 엣지만 남긴다", () => {
    seen.length = 0;
    render(<NetworkGraph people={people} coworks={coworks} entries={entries}
                         highlight={null} />);
    const data = seen.at(-1).graphData;
    // p000만 배치됐으므로 노드는 1개, 상대(p001)가 미배치라 엣지는 0개여야 한다.
    expect(data.nodes.map((n: any) => n.id)).toEqual(["p000"]);
    expect(data.links).toEqual([]);
  });

  it("두 끝점이 모두 배치되면 엣지를 남긴다", () => {
    seen.length = 0;
    const both = [...entries, { person_id: "p001", project_id: "j00", alloc: 1 }];
    render(<NetworkGraph people={people} coworks={coworks} entries={both}
                         highlight={null} />);
    const data = seen.at(-1).graphData;
    expect(data.nodes).toHaveLength(2);
    expect(data.links).toEqual([{ source: "p000", target: "p001", co_months: 9 }]);
  });

  it("컨테이너 실측 크기를 width/height로 넘긴다", () => {
    // 크기를 안 넘기면 force-graph가 window.innerWidth/innerHeight로 캔버스를
    // 깔고 노드를 그 중앙에 놓는다 -- 28rem 박스에서는 보이는 게 아무것도 없다.
    // jsdom의 getBoundingClientRect는 전부 0을 주므로 실제 박스를 흉내 낸다.
    const spy = vi.spyOn(Element.prototype, "getBoundingClientRect")
      .mockReturnValue({ width: 640, height: 448 } as DOMRect);
    try {
      seen.length = 0;
      render(<NetworkGraph people={people} coworks={coworks} entries={entries}
                           highlight={null} />);
      const props = seen.at(-1);
      expect(props.width).toBe(640);
      expect(props.height).toBe(448);
    } finally {
      spy.mockRestore();
    }
  });
});
