/** 인사팀 소명 "근거 한눈에"(2026-10-21 사용자 요청 -- 소명 글의 사실 수십 개를 표·그림으로)의 순수 계산.
 *  재료는 서버가 소명 글과 같은 재료에서 추린 `Justification.evidence`(core.kg.views.project_evidence) -- 화면은 다시 계산하지 않고 모양만 바꾼다. */
import type { JustEvidence } from "../api/types";

type Member = JustEvidence["members"][number];
type Req = Member["requirements"][number];
type Alt = JustEvidence["alternatives"][string][number];

const GRADE_ORDER = ["특급", "고급", "중급", "초급"];

/** 화면에 보일 이름(실데이터는 서버가 이름을 싣지 않는다 -- ID) */
export const nameOf = (m: { person_id: string; name: string | null }) => m.name ?? m.person_id;

/** 표의 줄 순서: 등급(특급→초급) → 이름 */
export function orderedMembers(ev: JustEvidence): Member[] {
  const g = (m: Member) => { const i = GRADE_ORDER.indexOf(m.grade ?? ""); return i < 0 ? 9 : i; };
  return [...ev.members].sort((a, b) => g(a) - g(b) || nameOf(a).localeCompare(nameOf(b), "ko"));
}

export interface Cell { mark: string; sub: string; tone: "ok" | "bad" | "none"; credited: string | null; title: string }

/** 충족표 한 칸: ✓ 레벨 3 / ✗ 레벨 2 (요구 3) / 없음. 하위 기술 경력에서 인정받았으면 출처·비율(credit^깊이)을 함께. */
export function cellOf(r: Req): Cell {
  const c = r.credited_from;
  const credited = c ? `${c.skill}${c.months != null ? ` ${c.months}개월` : ""}의 ${c.ratio != null ? `${Math.round(c.ratio * 100)}%` : "일부"} 인정` : null;
  const base = r.status === "met"
    ? { mark: `✓ 레벨 ${r.level}`, sub: `${r.months}개월`, tone: "ok" as const }
    : r.status === "below"
      ? { mark: `✗ 레벨 ${r.level}`, sub: `${r.months}개월 · 요구 ${r.min_level}`, tone: "bad" as const }
      : { mark: "없음", sub: `요구 레벨 ${r.min_level}`, tone: "none" as const };
  const title = `${r.skill}: 경력 ${r.months}개월 → 레벨 ${r.level}(요구 ${r.min_level})${credited ? ` · ${credited}` : ""}`;
  return { ...base, credited, title };
}

export interface SwapRow { member: Member; alt: Alt | null }

/** 팀원마다 가장 나은 대안(서버가 위반 없는·점수 높은 순으로 정렬해 보냄) -- 표의 줄 순서와 같게 */
export function bestSwaps(ev: JustEvidence): SwapRow[] {
  return orderedMembers(ev).map((m) => ({ member: m, alt: ev.alternatives[m.person_id]?.[0] ?? null }));
}

/** 점수 변화가 이보다 작으면 "같음"(표시 소수 둘째 자리에서 0.00) */
export const SAME = 0.005;

/** 대안 한 줄의 색: 위반 생김(노랑) · 오름(초록) · 내림(빨강) · 같음(회색) */
export function swapTone(a: Alt): "violation" | "up" | "down" | "same" {
  if (a.new_violations > 0) return "violation";
  return a.delta_total >= SAME ? "up" : a.delta_total <= -SAME ? "down" : "same";
}

export type VerdictKind = "some_better" | "better_only_with_violation" | "none_better" | "none";

/** 대안 결론. 위반이 생기는 대안은 "더 낫다"로 치지 않되, 점수가 오르는 위반 대안이 있으면 그렇다고 따로 말한다 --
 *  묶어서 "모두 내려간다"고 하면 글의 불리한 사실(ALT, 점수가 오르는 대안)을 뒤집었다(폴백 리뷰 MUST). */
export function swapVerdict(rows: SwapRow[]): { kind: VerdictKind; better: SwapRow[]; violating: SwapRow[] } {
  const withAlt = rows.filter((r) => r.alt);
  const better = withAlt.filter((r) => swapTone(r.alt!) === "up");
  const violating = withAlt.filter((r) => r.alt!.new_violations > 0 && r.alt!.delta_total >= SAME);
  const kind: VerdictKind = !withAlt.length ? "none" : better.length ? "some_better"
    : violating.length ? "better_only_with_violation" : "none_better";
  return { kind, better, violating };
}

/** 협업 지도에 쓸 명단(이 사업 팀만) -- PDF 팀별 협업 지도(report/TeamNetworks.teamCards)에 그대로 넣는다 */
export function teamEntries(ev: JustEvidence): { person_id: string; project_id: string }[] {
  return ev.members.map((m) => ({ person_id: m.person_id, project_id: ev.project_id }));
}
