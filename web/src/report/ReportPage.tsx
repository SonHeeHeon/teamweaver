import { useEffect, useMemo, useState } from "react";
import { fetchMeta } from "../api/client";
import type { Meta } from "../api/types";
import { StaticNetwork } from "./StaticNetwork";

declare global {
  interface Window {
    __REPORT_DATA__?: any;
    __REPORT_READY__?: boolean;
  }
}

/** 인쇄 전용 페이지. Playwright가 add_init_script로 window.__REPORT_DATA__를
 *  먼저 심고 이 라우트로 이동한다. 레이아웃이 끝나면 __REPORT_READY__를 세워
 *  "이제 인쇄해도 된다"를 알린다. */
export function ReportPage() {
  const data = window.__REPORT_DATA__;
  // 서버가 PDF 요청 시점의 데이터셋으로 만든 meta를 함께 넣어 준다(K9) -- 있으면 그것만
  // 쓴다. 따로 /api/meta를 부르면 렌더 중 전환된 새 데이터의 이름이 옛 명단에 붙는다.
  const [meta, setMeta] = useState<Meta | null>(() => (data?.meta as Meta | undefined) ?? null);

  useEffect(() => {
    if (window.__REPORT_DATA__?.meta) return;
    fetchMeta().then(setMeta).catch(() => setMeta(null));
  }, []);

  const graph = useMemo(() => {
    if (!meta || !data) return { nodes: [], links: [] };
    const placed = new Set<string>((data.entries ?? []).map((e: any) => e.person_id));
    const byId = new Map(meta.people.map((p) => [p.id, p]));
    return {
      nodes: [...placed].filter((id) => byId.has(id))
        .map((id) => ({ id, name: byId.get(id)!.name })),
      links: meta.coworks
        .filter((c) => placed.has(c.a_id) && placed.has(c.b_id))
        .map((c) => ({ source: c.a_id, target: c.b_id, co_months: c.co_months })),
    };
  }, [meta, data]);

  useEffect(() => {
    // meta와 data가 모두 있고 그래프 좌표까지 계산된 뒤에야 준비 완료다.
    // data 없이 __REPORT_READY__를 세우면 "리포트 데이터가 없다"는 빈 페이지도
    // 정상 완료로 신호하게 돼, Playwright가 그걸 그대로 PDF로 찍어낸다.
    if (meta && data) window.__REPORT_READY__ = true;
  }, [meta, data, graph]);

  if (!data) return <p className="p-8">리포트 데이터가 없다.</p>;

  const pct = (v: number) => `${((v ?? 0) * 100).toFixed(1)}%`;
  const byId = new Map((meta?.people ?? []).map((p) => [p.id, p]));
  const jName = new Map((meta?.projects ?? []).map((j) => [j.id, j.name]));
  const nameOf = (id: string) => byId.get(id)?.name ?? id;
  const generatedAt = new Date().toLocaleString("ko-KR");

  return (
    <div className="mx-auto max-w-[760px] bg-white p-8 text-slate-900">
      <h1 className="text-2xl font-bold">TeamWeaver 인력 배치 리포트</h1>
      <p className="mt-1 text-sm text-slate-500">Plan {data.plan_label}</p>
      <p className="text-xs text-slate-400">생성 시각 {generatedAt}</p>
      {meta && data.dataset_version && meta.dataset_version !== data.dataset_version && (
        <p className="mt-1 rounded border border-red-300 bg-red-50 px-2 py-1 text-xs text-red-800">
          경고: 이 명단을 계산한 데이터셋과 지금 서버의 데이터셋이 다르다. 이름·등급 표시가 틀릴 수 있다.
        </p>
      )}
      <p className="text-xs text-slate-500">
        계산 기준: {data.milp_params
          ? `최소 투입률 ${Math.round(data.milp_params.min_alloc * 10000) / 100}% · `
            + `반복 협업 기준 ${data.milp_params.clique_threshold_months}개월 · `
            + `협업 가중 ${data.milp_params.lam} · 반복 협업 감점 ${data.milp_params.mu}`
          : "서버 모델 기본값(배치 설정 미적용)"}
      </p>

      <section className="mt-6 grid grid-cols-3 gap-4">
        {[["최적화율", pct(data.optimization_ratio)],
          ["매칭 충족률", pct(data.fulfillment)],
          ["배치 인원", `${(data.entries ?? []).length}명`]].map(([k, v]) => (
          <div key={k} className="rounded-lg border border-slate-200 p-3">
            <div className="text-xs text-slate-500">{k}</div>
            <div className="mt-1 text-xl font-semibold tabular-nums">{v}</div>
          </div>
        ))}
      </section>

      <section className="mt-6">
        <h2 className="mb-2 text-base font-semibold">협업 네트워크</h2>
        <StaticNetwork nodes={graph.nodes} links={graph.links} />
      </section>

      {data.briefing && (
        <section className="mt-6">
          <h2 className="mb-2 text-base font-semibold">XAI 브리핑</h2>
          {data.swap && (
            <p className="text-xs text-slate-500">
              검토한 교체: {nameOf(data.swap.out_person_id)} → {nameOf(data.swap.in_person_id)}
              {typeof data.objective_delta === "number" &&
                ` (현행 점수 기준 Δ ${data.objective_delta >= 0 ? "+" : ""}`
                + `${data.objective_delta.toFixed(4)}, 참고값·재최적화 아님)`}
            </p>
          )}
          {(data.swap_violations ?? []).length > 0 && (
            <div className="mt-2 rounded border border-red-300 bg-red-50 px-3 py-2
                            text-sm text-red-800">
              <p className="font-medium">교체 검토 경고</p>
              <ul className="list-inside list-disc">
                {data.swap_violations.map((w: string) => <li key={w}>{w}</li>)}
              </ul>
            </div>
          )}
          {data.fallback_used && (
            <p className="mt-2 rounded border border-amber-400 bg-amber-50 px-3 py-2
                          text-sm font-medium text-amber-800">
              이 브리핑은 LLM이 아니라 규칙 기반(결정론적) 생성기로 만들어졌다 --
              발표 시점에 LLM을 사용할 수 없었다.
            </p>
          )}
          <p className="mt-2 text-sm">{data.briefing.rationale}</p>
          <h3 className="mt-3 text-xs font-medium text-slate-500">리스크</h3>
          <ul className="list-inside list-disc text-sm">
            {(data.briefing.risks ?? []).map((r: string) => <li key={r}>{r}</li>)}
          </ul>
          <h3 className="mt-3 text-xs font-medium text-slate-500">대안</h3>
          <ul className="list-inside list-disc text-sm">
            {(data.briefing.alternatives ?? []).map((a: string) => <li key={a}>{a}</li>)}
          </ul>
        </section>
      )}

      <section className="mt-6">
        <h2 className="mb-2 text-base font-semibold">배치 명단</h2>
        <table className="w-full text-sm">
          <thead className="border-b border-slate-300 text-left text-slate-600">
            <tr><th className="py-1">인력</th><th className="py-1">등급</th>
                <th className="py-1">프로젝트</th><th className="py-1 text-right">투입률</th></tr>
          </thead>
          <tbody>
            {(data.entries ?? []).map((e: any) => (
              <tr key={`${e.person_id}-${e.project_id}`} className="border-b border-slate-100">
                <td className="py-1">{byId.get(e.person_id)?.name ?? e.person_id}</td>
                <td className="py-1 text-slate-600">{byId.get(e.person_id)?.grade ?? "-"}</td>
                <td className="py-1 text-slate-600">{jName.get(e.project_id) ?? e.project_id}</td>
                <td className="py-1 text-right tabular-nums">{(e.alloc * 100).toFixed(0)}%</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}
