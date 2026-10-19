/** 신규 사업 편성: "넣기로 고른 사람" 줄과 후보 표 읽는 법(2026-10-20 사용자 요청 2·4·5번, claude-b).
 *  고른 사람은 확정 전에도 탭 위에 보이고, 서버가 그 사람들을 팀원으로 쳐서 자리·점수·위반을 미리 계산한다(`Candidates.draft`).
 *  데이터는 확정 때만 바뀐다. */
import { Chip, GRADE_TONE } from "../ui";
import { signedPts } from "../components/scoreSplit";
import { duosText, seatLines } from "./pickView";
import type { CandidateRow, DraftSummary } from "./workspaceApi";

interface DraftProps {
  projectId: string;
  picks: CandidateRow[];
  summary: DraftSummary | null | undefined;
  /** 고른 사람을 바꾼 뒤 다시 계산하는 중 */
  recalculating: boolean;
  /** 지금 선택의 미리 계산이 실패했다(이전 결과는 보이지 않는다) */
  failed?: boolean;
  onRetry?: () => void;
  /** 사업 번호 → "J013 사업명" */
  named?: (projectId: string) => string;
  onRemove: (personId: string) => void;
}

export function PickDraft({ projectId, picks, summary, recalculating, failed, onRetry, named = (id) => id, onRemove }: DraftProps) {
  if (!picks.length) return null;
  const lines = summary ? seatLines(summary.seat_effects, projectId) : [];
  return (
    <section aria-label="넣기로 고른 사람" className="rounded-md border border-pine-200 bg-pine-50/60 px-3 py-2.5 text-sm">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className="font-medium text-pine-900">넣기로 고른 사람 {picks.length}명</span>
        <span className="text-xs text-slate-500">확정 전 미리 계산 · 확정해야 데이터가 바뀝니다</span>
        {recalculating && <span className="text-xs text-slate-500">다시 계산하는 중…</span>}
        {failed && (
          <span className="text-xs text-amber-800">미리 계산을 하지 못했습니다(확정은 계산이 끝난 뒤에)
            {onRetry && <button type="button" className="btn ml-1.5 px-1.5 py-0.5 text-xs" onClick={onRetry}>다시 계산</button>}</span>
        )}
      </div>
      <ul className="mt-1.5 flex flex-wrap gap-1.5">
        {picks.map((r) => (
          <li key={r.person_id} className="inline-flex items-center gap-1 rounded border border-slate-200 bg-white px-1.5 py-0.5">
            <Chip tone={GRADE_TONE[r.grade]}>{r.grade}</Chip>
            <span>{r.name}</span>
            {r.pulled_from.length > 0 && <span className="text-xs text-amber-700">{r.pulled_from.map(named).join(", ")}에서 빼 옴</span>}
            <button type="button" className="ml-0.5 px-1 text-xs text-slate-500 hover:text-red-700" aria-label={`${r.name} 빼기`}
                    onClick={() => onRemove(r.person_id)}>✕</button>
          </li>
        ))}
      </ul>
      {summary && (
        <div className={`mt-2 space-y-1 text-xs text-slate-600 ${recalculating ? "opacity-60" : ""}`}>
          <p>
            <span className="text-slate-500">정원 변화 </span>
            {lines.length ? lines.map((l, i) => (
              <span key={i} className={`mr-2 ${l.tone === "warn" ? "text-amber-700" : "text-pine-800"}`} title={l.title}>{l.text}</span>
            )) : <span>없음(정원 밖 등급이거나 이미 찬 등급)</span>}
            <span className="text-slate-500"> · 기술 </span><span className="num">{signedPts(summary.skill_delta)}</span>
            <span className="text-slate-500"> · 협업 </span><span className="num">{signedPts(summary.collab_delta)}</span>
          </p>
          {summary.pairs.length > 0 && (
            <p><span className="text-slate-500">함께하게 될 쌍 </span>
              {duosText(summary.pairs)}
            </p>
          )}
          {summary.new_violations.length > 0 && (
            <p className="text-amber-800">확정하면 규칙 위반: {summary.new_violations.join(" / ")}</p>
          )}
        </div>
      )}
    </section>
  );
}

/** 후보 표 읽는 법 -- 처음 쓰는 담당자용(사용자 2026-10-20 "'1자리 메움','1자리 생김'이 어떤 의미인지 잘 모를거같으니"). */
export function CandidateGuide() {
  return (
    <details className="text-xs text-slate-600">
      <summary className="cursor-pointer select-none text-slate-500 hover:text-slate-700">표 읽는 법(정원 변화 · 기술 · 협업)</summary>
      <dl className="mt-1.5 grid gap-x-3 gap-y-1 sm:grid-cols-[88px_1fr]">
        <dt className="font-medium text-slate-700">정원 변화</dt>
        <dd>사업마다 등급별로 필요한 사람 수(자리)가 정해져 있습니다. <b>이 사업 1자리 채움</b> = 이 사람이 비어 있던 자리 하나를 채웁니다.
          <b> J013에 1자리 빔</b> = 이 사람을 빼 오면 원래 사업 J013의 자리 하나가 빕니다. 비어 있는 자리 하나는 계산 점수로 100점이라
          기술·협업 점수와 따로 보입니다. "—"는 그 등급 자리가 이미 찼거나 사업이 그 등급을 요구하지 않는다는 뜻입니다.</dd>
        <dt className="font-medium text-slate-700">기술</dt>
        <dd>이 사업이 요구하는 기술에 이 사람의 경력·숙련도가 얼마나 맞는지입니다.</dd>
        <dt className="font-medium text-slate-700">협업</dt>
        <dd>협업 점수는 <b>사람 한 명의 점수가 아니라 두 사람 사이의 점수</b>입니다(함께 일한 개월과 서로 남긴 동료 평가).
          그래서 이 사업에 이미 있는 팀원과 <b>넣기로 고른 사람</b>과의 쌍만 셉니다 — 팀이 비어 있으면 0이고, 한 명을 고르면 다음 후보부터 그 사람과의 궁합이 보입니다.
          최근 몇 년 안에 오래 함께한 사이(설정 화면 기준, 기본 3년 중 12개월 이상)는 "익숙한 쌍"으로 조금 감점합니다(새 조합을 권하는 규칙). 빼 오는 사람은 원래 사업에서 잃는 협업도 빠집니다.</dd>
      </dl>
      <p className="mt-1 text-slate-400">순서는 정원·기술·협업을 모두 더한 값으로 정합니다. 계산상 값이며 실제 성과는 검증 전입니다.</p>
    </details>
  );
}
