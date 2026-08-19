import { useLayoutEffect, useMemo, useRef, useState } from "react";
import ForceGraph2D from "react-force-graph-2d";
import type { AssignEntry, Cowork, Person } from "../api/types";

interface Props {
  people: Person[];
  coworks: Cowork[];
  entries: AssignEntry[];
  highlight: string | null;
}

/** ForceGraph2D의 width/height 기본값은 `window.innerWidth`/`innerHeight`다
 *  (force-graph/dist/force-graph.mjs의 Kapsule props). 즉 크기를 넘기지 않으면
 *  28rem 박스 안에 뷰포트 크기 캔버스가 깔리고, 노드는 그 캔버스 중앙 --
 *  보이는 영역 바깥 -- 에 모인다. 화면은 조용히 빈 흰 상자가 된다.
 *  그래서 컨테이너를 직접 재서 명시적으로 넘긴다. */
function useBoxSize() {
  const ref = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => {
      const r = el.getBoundingClientRect();
      setSize({ w: Math.round(r.width), h: Math.round(r.height) });
    };
    measure();
    // jsdom에는 ResizeObserver가 없다 -- 없으면 최초 측정만으로 끝낸다.
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  return { ref, size };
}

/** 현재 플랜에 배치된 인력만 그린다 -- 100명 전체를 그리면 배치와 무관한 노드가
 *  대부분이라 "이 배치의 협업 구조"를 읽을 수 없다. 엣지는 두 끝점이 모두
 *  배치돼 있을 때만 남긴다(한쪽이 없으면 선이 허공으로 나간다). */
export function NetworkGraph({ people, coworks, entries, highlight }: Props) {
  const { ref, size } = useBoxSize();

  const graphData = useMemo(() => {
    const placed = new Set(entries.map((e) => e.person_id));
    const byId = new Map(people.map((p) => [p.id, p]));
    const nodes = [...placed].filter((id) => byId.has(id)).map((id) => {
      const p = byId.get(id)!;
      return { id, name: p.name, grade: p.grade };
    });
    const links = coworks
      .filter((c) => placed.has(c.a_id) && placed.has(c.b_id))
      .map((c) => ({ source: c.a_id, target: c.b_id, co_months: c.co_months }));
    return { nodes, links };
  }, [people, coworks, entries]);

  return (
    <div ref={ref}
         className="h-[28rem] overflow-hidden rounded-lg border border-slate-200 bg-white">
      <ForceGraph2D
        width={size.w}
        height={size.h}
        graphData={graphData}
        nodeLabel={(n: any) => `${n.name} (${n.grade})`}
        nodeColor={(n: any) => (n.id === highlight ? "#0f172a" : "#94a3b8")}
        nodeRelSize={5}
        linkWidth={(l: any) => Math.max(1, Math.min(5, l.co_months / 3))}
        linkColor={() => "#cbd5e1"}
        cooldownTicks={120}
      />
    </div>
  );
}
