import { useMemo } from "react";
import { forceSimulation, forceLink, forceManyBody, forceCenter, forceX, forceY } from "d3-force";

export interface StaticNode { id: string; name: string }
export interface StaticLink { source: string; target: string; co_months: number }

interface Props {
  nodes: StaticNode[];
  links: StaticLink[];
  width?: number;
  height?: number;
}

/** 인쇄용 정적 네트워크.
 *
 *  대화형 탭의 react-force-graph-2d(canvas, 애니메이션)를 쓰지 않는 이유가
 *  두 가지다. (1) canvas는 PDF에 래스터로 박히지만 SVG는 벡터로 남는다.
 *  (2) 물리 시뮬레이션은 '언제 정착했는가'가 불확실해 Playwright가 무엇을
 *  기다려야 할지 정의할 수 없다. 여기서는 d3-force를 동기적으로 N틱 돌려
 *  좌표를 확정한 뒤 한 번에 그린다 -- 렌더 시점에 이미 최종 상태다.
 *  대화형 그래프와의 중복은 의도된 것이다(아키텍처 결정 2). */
export function StaticNetwork({ nodes, links, width = 700, height = 480 }: Props) {
  const laid = useMemo(() => {
    const ns = nodes.map((n) => ({ ...n, x: 0, y: 0 }));
    const ls = links.map((l) => ({ ...l }));
    const sim = forceSimulation(ns as any)
      .force("link", forceLink(ls as any).id((d: any) => d.id).distance(60))
      .force("charge", forceManyBody().strength(-160))
      .force("center", forceCenter(width / 2, height / 2))
      // forceCenter는 무게중심만 옮길 뿐 흩어짐을 막지 못한다. 협업 이력이 없는
      // 고립 노드는 charge에 밀려 수천 px 밖으로 날아가고, 그 상태로 viewBox를
      // 실제 범위에 맞추면 본 클러스터가 점으로 줄어든다(실측). 약한 x/y
      // 복원력으로 전체를 유한한 영역에 묶어 둔다.
      .force("x", forceX(width / 2).strength(0.09))
      .force("y", forceY(height / 2).strength(0.09))
      .stop();
    sim.tick(300);                 // 동기 300틱 -- 애니메이션 없이 확정 좌표

    // 노드 수가 늘면 레이아웃이 고정 뷰포트를 넘어 퍼지고 SVG는 밖을 잘라낸다
    // (실측: 78노드에서 좌우 노드·라벨이 잘렸다). 확정된 좌표의 실제 범위에
    // viewBox를 맞춰 항상 전부 들어오게 한다. 오른쪽 여백을 크게 주는 이유는
    // 라벨이 노드 오른쪽으로 뻗기 때문이다.
    const PAD = 16, PAD_RIGHT = 64;
    const xs = ns.map((n) => n.x), ys = ns.map((n) => n.y);
    const viewBox = xs.length === 0
      ? `0 0 ${width} ${height}`
      : [Math.min(...xs) - PAD, Math.min(...ys) - PAD,
         Math.max(...xs) - Math.min(...xs) + PAD + PAD_RIGHT,
         Math.max(...ys) - Math.min(...ys) + PAD * 2].join(" ");
    return { ns: ns as any[], ls: ls as any[], viewBox };
  }, [nodes, links, width, height]);

  return (
    <svg width={width} height={height} viewBox={laid.viewBox}
         preserveAspectRatio="xMidYMid meet" className="bg-white">
      {laid.ls.map((l, i) => (
        <line
          key={i}
          x1={l.source.x} y1={l.source.y} x2={l.target.x} y2={l.target.y}
          stroke="#cbd5e1"
          strokeWidth={Math.max(1, Math.min(4, l.co_months / 3))}
        />
      ))}
      {laid.ns.map((n) => (
        <g key={n.id}>
          <circle cx={n.x} cy={n.y} r={5} fill="#475569" />
          <text x={n.x + 8} y={n.y + 4} fontSize={9} fill="#334155">{n.name}</text>
        </g>
      ))}
    </svg>
  );
}
