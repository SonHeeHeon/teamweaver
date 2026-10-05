import type { Evidence } from "../api/types";

const BADGE: Record<Evidence["kind"], { label: string; cls: string }> = {
  quote: { label: "직접 인용", cls: "bg-emerald-50 text-emerald-800 border-emerald-200" },
  summary: { label: "요약", cls: "bg-sky-50 text-sky-800 border-sky-200" },
  label: { label: "원문 비공개(실데이터)", cls: "bg-slate-100 text-slate-700 border-slate-300" },
};

interface Props {
  evidence: Evidence[];
  /** 본문(근거·리스크·대안)에 [rv:…] 출처 표시가 있는지 -- 있으면 그 뜻을 안내한다. */
  hasInlineMarkers?: boolean;
  nameOf?: (personId: string) => string;
}

/** 브리핑 근거 목록(K5). 화면과 PDF가 같은 표시를 쓴다.
 *  quote는 원문과 글자 그대로 대조된 문장이라 따옴표로 감싸고, summary는 파서가 바꿔 쓴 문장이라
 *  인용으로 보이지 않게 한다. label은 실데이터라 원문 대신 리뷰 항목 라벨만 보인다. */
export function EvidenceList({ evidence, hasInlineMarkers = false, nameOf = (id) => id }: Props) {
  if (evidence.length === 0 && !hasInlineMarkers) return null;
  return (
    <div className="mt-3">
      <h4 className="text-xs font-medium text-slate-500">근거</h4>
      <ul className="mt-1 space-y-1.5 text-sm text-slate-700">
        {evidence.map((e, i) => {
          const b = BADGE[e.kind];
          return (
            <li key={`${i}-${e.source_id}`}>
              <span className={`mr-1.5 rounded border px-1.5 py-0.5 text-[11px] ${b.cls}`}>{b.label}</span>
              {e.kind === "quote" ? <q>{e.text}</q> : <span>{e.text}</span>}
              <span className="ml-1.5 text-[11px] text-slate-400">
                {nameOf(e.reviewer_id)} 리뷰 · {e.source_id}
              </span>
            </li>
          );
        })}
      </ul>
      {hasInlineMarkers && (
        <p className="mt-1 text-[11px] text-slate-400">
          본문의 [rv:…] 표시는 관련 출처를 가리킬 뿐, 그 문장 전체가 원문으로 검증됐다는 뜻은 아니다.
        </p>
      )}
    </div>
  );
}
