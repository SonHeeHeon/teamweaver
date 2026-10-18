import { formatPrecomputedAt } from "./precomputed";

interface Props {
  /** 미리 계산 시각(ISO, 비어 있을 수도 있다). null이면 같은 조건으로 앞서 계산해 둔 저장 결과다. */
  at: string | null;
  /** 편성 기록에서 연 결과면 그 기록 제목. */
  label?: string | null;
  onRecompute: () => void;
  /** 다시 계산 버튼 옆 안내(예: 걸리는 시간). */
  hint?: string;
  disabled?: boolean;
}

/** 결과가 방금 계산한 것이 아님을 밝히고 "다시 계산"을 준다(시연 정직성, claude-a 리허설 요청 2026-10-07). */
export function PrecomputedNotice({ at, label, onRecompute, hint, disabled }: Props) {
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-md border border-sky-200
                    bg-sky-50 px-4 py-2 text-sm text-sky-900">
      <span role="status">
        {label
          ? <>편성 기록 <b>“{label}”</b>에서 연 결과입니다{at ? <> (계산 <span title={formatPrecomputedAt(at, true)}>{formatPrecomputedAt(at)}</span>)</> : null}.
              서버가 이 데이터로 다시 채점해 규칙 위반이 없음을 확인했습니다.</>
          : at != null
          ? <>이 결과는 시연용으로 <b title={formatPrecomputedAt(at, true)}>{formatPrecomputedAt(at)}</b>에 <b>미리 계산</b>해 둔 것이다(방금 계산한 것이 아니다).
              서버가 같은 데이터·같은 설정임을 확인했다.</>
          : "이 결과는 같은 조건으로 앞서 계산해 둔 저장 결과다."}
      </span>
      <button onClick={onRecompute} disabled={disabled}
              className="rounded-md border border-sky-300 bg-white px-3 py-1 text-xs font-medium text-sky-900
                         hover:bg-sky-100 disabled:text-slate-400">
        다시 계산
      </button>
      <span className="text-xs text-sky-700">{hint ?? "처음부터 다시 푼다 — 인원·설정에 따라 몇 분 걸릴 수 있다."}</span>
    </div>
  );
}
