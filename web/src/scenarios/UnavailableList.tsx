/** 그 사업 기간에 투입할 수 없는 인력(2026-10-19 사용자 지시: 육아휴직 등) -- 후보 계산에서는 빠지지만, 화면에는 회색으로 보이고
 *  넣기 버튼은 막히며 사유는 빨간 글씨다. 신규 사업 편성·긴급 투입 화면이 함께 쓴다. */
import { Chip, GRADE_TONE } from "../ui";
import type { UnavailableRow } from "./workspaceApi";

export function UnavailableList({ rows }: { rows?: UnavailableRow[] }) {
  if (!rows?.length) return null;
  return (
    <div className="rounded-md border border-red-100 bg-red-50/40 px-3 py-2" aria-label="투입 불가 인력">
      <p className="text-xs font-medium text-slate-600">이 사업 기간에 투입할 수 없는 인력 {rows.length}명</p>
      <ul className="mt-1 space-y-1 text-sm">
        {rows.map((r) => (
          <li key={r.person_id} className="flex items-center justify-between gap-2 opacity-90">
            <span className="text-slate-400">
              <span className="mr-1.5"><Chip tone={GRADE_TONE[r.grade]}>{r.grade}</Chip></span>
              <span className="line-through">{r.name}</span>
              <span className="ml-2 text-xs font-medium text-red-600">{r.reason}</span>
            </span>
            <button className="btn cursor-not-allowed px-2 py-0.5 text-xs opacity-50" disabled aria-label={`${r.name} 넣기(투입 불가)`}>
              넣기
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
