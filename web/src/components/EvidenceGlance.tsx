import type { JustEvidence, Meta } from "../api/types";
import { TeamCircleCard, WIDE_FROM, teamCards } from "../report/TeamNetworks";
import { Chip, GRADE_TONE } from "../ui";
import { RelationLegend } from "./RelationLegend";
import { bestSwaps, cellOf, nameOf, orderedMembers, swapTone, swapVerdict, teamEntries } from "./evidenceView";

/** 인사팀 소명 "근거 한눈에"(2026-10-21 사용자 요청 -- 소명 글의 사실 수십 개를 표·그림으로, 지식 그래프 근거).
 *  ① 요구 기술 충족표(팀원 × 요구 기술) ② 팀 협업 지도(PDF 팀별 협업 지도와 같은 그림·색) ③ 다른 사람으로 바꾸면(대안 막대).
 *  글과 같은 재료(표의 기술 적합만 소수 둘째 자리로 줄임)에서 주요 사실만 모은다 -- 동료 평가 글·고객사·규칙 준수 사실은 글에만. AI를 쓰지 않는다. */

const TONE_CLS = { ok: "bg-emerald-50 text-emerald-800", bad: "bg-red-50 text-red-700", none: "bg-slate-100 text-slate-500" };
const BAR_CLS = { violation: "bg-amber-400", up: "bg-emerald-500", down: "bg-red-500", same: "bg-slate-400" };
const NUM_CLS = { violation: "text-amber-700", up: "text-emerald-700", down: "text-red-700", same: "text-slate-500" };
// 13명(WIDE_FROM)부터는 PDF 원 그림이 한 줄 폭(640)이라 키우지 않는다 -- 1.9배면 반 칸을 넘어 옆 그림 위에 겹쳤다(폴백 리뷰 SHOULD)

function RequirementTable({ ev }: { ev: JustEvidence }) {
  const members = orderedMembers(ev);
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[40rem] text-sm" aria-label="요구 기술 충족표">
        <thead className="text-xs text-slate-500">
          <tr>
            <th className="py-1 pr-2 text-left font-medium">팀원</th>
            {ev.requirements.map((r) => (
              <th key={r.skill} className="px-1 text-center font-medium">
                <span className="text-slate-800">{r.skill}</span>
                <span className="block text-[11px] font-normal">레벨 {r.min_level} 이상 · {r.headcount}명 필요</span>
              </th>
            ))}
            <th className="px-1 text-center font-medium" title="이 사업과 같은 산업의 과거 사업 수(그 사람이 그 산업 사업에서 일한 개월)">같은 산업<span className="block text-[11px] font-normal">과거 사업</span></th>
            <th className="pl-1 text-right font-medium" title="이 사업 요구 기술에 맞는 정도(0~1)">기술 적합</th>
          </tr>
        </thead>
        <tbody>
          {members.map((m) => {
            const by = new Map(m.requirements.map((r) => [r.skill, r]));
            return (
              <tr key={m.person_id} className="border-t border-slate-100">
                <td className="whitespace-nowrap py-1.5 pr-2">
                  {m.grade && GRADE_TONE[m.grade] && <span className="mr-1"><Chip tone={GRADE_TONE[m.grade]}>{m.grade}</Chip></span>}
                  <span className="font-medium text-slate-900">{nameOf(m)}</span>
                </td>
                {ev.requirements.map((rq) => {
                  const r = by.get(rq.skill);
                  if (!r) return <td key={rq.skill} />;
                  const c = cellOf(r);
                  return (
                    <td key={rq.skill} className="px-1 py-1 text-center" title={c.title}>
                      <span className={`inline-block rounded px-1.5 py-px text-xs font-semibold ${TONE_CLS[c.tone]}
                                        ${c.credited ? "outline-dashed outline-1 outline-amber-500" : ""}`}>{c.mark}</span>
                      <span className="block text-[11px] text-slate-500">{c.sub}</span>
                    </td>
                  );
                })}
                <td className="px-1 text-center text-xs">
                  {m.same_industry_projects == null ? "–" : <>{m.same_industry_projects}건
                    <span className="block text-[11px] text-slate-500">{m.same_industry_months ?? 0}개월</span></>}
                </td>
                <td className="num pl-1 text-right text-xs">{m.skill_fit == null ? "–" : m.skill_fit.toFixed(2)}</td>
              </tr>
            );
          })}
          <tr className="border-t border-slate-200 bg-slate-50 text-xs">
            <td className="py-1.5 pr-2 font-semibold text-slate-700">사업 요구</td>
            {ev.requirements.map((r) => (
              <td key={r.skill} className={`px-1 text-center font-semibold ${r.status === "met" ? "text-emerald-700" : "text-red-700"}`}>
                충족 {r.met_by}명 / 필요 {r.headcount}명
              </td>
            ))}
            <td /><td />
          </tr>
        </tbody>
      </table>
      <p className="mt-1 text-[11px] text-slate-500">
        레벨 = 경력 개월(12·36·60·96개월 경계). 점선 테두리 = 하위 기술 경력에서 일부 인정받은 경력(마우스를 올리면 출처·비율).
      </p>
    </div>
  );
}

function SwapBars({ ev }: { ev: JustEvidence }) {
  const rows = bestSwaps(ev);
  const verdict = swapVerdict(rows);
  const max = Math.max(0.25, ...rows.map((r) => Math.abs(r.alt?.delta_total ?? 0)));
  const HALF = 110;
  const pair = (r: (typeof rows)[number]) => `${nameOf(r.member)} → ${nameOf(r.alt!)}`;
  return (
    <div>
      <p className="text-[11px] text-slate-500">
        팀원마다 같은 등급에서 이 사업 기술이 맞는 상위 5명을 한 명씩 넣어 보고, 그중 가장 나은 사람으로 바꿨을 때 전체 점수가 얼마나 바뀌는지 다시 잰 값입니다
        (여러 명을 함께 바꾸거나 다시 최적화한 것은 아닙니다).
      </p>
      <ul className="mt-2 space-y-1.5" aria-label="다른 사람으로 바꾸면">
        {rows.map((r) => {
          const { member, alt } = r;
          const d = alt?.delta_total ?? 0, w = (Math.abs(d) / max) * HALF;
          const tone = alt ? swapTone(alt) : "same";
          return (
            <li key={member.person_id} className="flex items-center gap-2 text-xs" data-tone={alt ? tone : undefined}>
              <span className="w-32 shrink-0 truncate" title={alt ? pair(r) : undefined}>
                <b className="text-slate-800">{nameOf(member)}</b>{alt ? <span className="text-slate-500"> → {nameOf(alt)}</span> : null}
              </span>
              {alt ? (
                <>
                  <span className="relative h-3 shrink-0 rounded-sm bg-slate-100" style={{ width: 2 * HALF }} aria-hidden="true">
                    <span className="absolute top-[-3px] h-[18px] w-px bg-slate-500" style={{ left: HALF }} />
                    <span className={`absolute h-3 rounded-sm ${BAR_CLS[tone]}`}
                          style={{ left: d < 0 ? HALF - w : HALF, width: Math.max(w, 1) }} />
                  </span>
                  <span className={`num w-12 shrink-0 text-right font-semibold ${NUM_CLS[tone]}`}>
                    {d >= 0 ? "+" : ""}{d.toFixed(2)}</span>
                  <span className="truncate text-[11px] text-slate-500">
                    기술 {alt.delta_skill >= 0 ? "+" : ""}{alt.delta_skill.toFixed(2)} · 협업 {alt.delta_synergy >= 0 ? "+" : ""}{alt.delta_synergy.toFixed(2)}
                    {alt.new_violations > 0 && <span className="font-semibold text-amber-700"> · 규칙 위반 생김</span>}
                  </span>
                </>
              ) : <span className="text-slate-400">비교할 같은 등급 대안 없음</span>}
            </li>
          );
        })}
      </ul>
      <p className={`mt-2 text-xs font-semibold ${verdict.kind === "none_better" ? "text-emerald-700"
        : verdict.kind === "none" ? "text-slate-500" : "text-amber-700"}`} data-verdict={verdict.kind}>
        {verdict.kind === "none_better" && "비교한 대안 중 규칙 위반 없이 점수가 오르는 경우는 없습니다(±0.005 안의 변화는 같음으로 봄)."}
        {verdict.kind === "some_better" && `규칙 위반 없이 점수가 오르는 대안이 있습니다: ${verdict.better.map(pair).join(", ")} — 글의 '불리한 사실'에도 적혀 있습니다.`}
        {verdict.kind === "better_only_with_violation" && `점수가 오르는 대안(${verdict.violating.map(pair).join(", ")})은 모두 규칙 위반(가용률·겹침 등)이 생깁니다.`}
        {verdict.kind === "none" && "비교할 대안이 없습니다."}
      </p>
    </div>
  );
}

export function EvidenceGlance({ evidence, meta }: { evidence: JustEvidence; meta: Pick<Meta, "people" | "projects" | "coworks"> }) {
  const raw = teamCards(teamEntries(evidence), meta.people, meta.projects, meta.coworks ?? [])[0];
  // 이름은 표와 같은 표기(실데이터면 ID) -- meta의 이름을 그대로 쓰면 같은 사람이 ①은 ID, ②는 이름으로 보였다(폴백 리뷰 SHOULD)
  const shown = new Map(evidence.members.map((m) => [m.person_id, nameOf(m)]));
  const card = raw && { ...raw, members: raw.members.map((m) => ({ ...m, name: shown.get(m.id) ?? m.id })) };
  const hasReview = !!card?.ties.some((t) => t.tone !== "none");
  return (
    <section className="mt-3 rounded-md border border-slate-200 px-4 py-3" aria-label="근거 한눈에">
      <p className="text-sm font-semibold text-slate-900">근거 한눈에</p>
      <p className="mb-2 text-[12px] text-slate-500">아래 '전체 소명'의 주요 사실(요구 기술·같은 산업·팀 관계·대안)을 표·그림으로 모았습니다 — 글과 같은 재료이고, AI를 쓰지 않습니다.</p>
      <p className="mb-1 text-xs font-semibold text-slate-700">① 요구 기술 충족 — 팀원 × 요구 기술</p>
      <RequirementTable ev={evidence} />
      <div className="mt-3 grid gap-4 lg:grid-cols-2">
        <div>
          <p className="mb-1 text-xs font-semibold text-slate-700">② 팀 협업 지도 — 이 팀 안에서 예전에 함께 일한 사이</p>
          {card ? (
            <div className="overflow-x-auto">
              <TeamCircleCard card={card} scale={card.members.length >= WIDE_FROM ? 1 : 1.9} fit />
              <RelationLegend hasReview={hasReview} extra={
                <span><svg width={10} height={10} className="mr-1 inline-block align-middle"><circle cx={5} cy={5} r={3.5} fill="#fff" stroke="#64748b" strokeWidth={1.5} /></svg>팀에 아는 사람 없음</span>} />
            </div>
          ) : <p className="text-xs text-slate-500">팀원이 없습니다.</p>}
        </div>
        <div>
          <p className="mb-1 text-xs font-semibold text-slate-700">③ 다른 사람으로 바꾸면 — 팀원별 가장 나은 대안</p>
          <SwapBars ev={evidence} />
        </div>
      </div>
    </section>
  );
}
