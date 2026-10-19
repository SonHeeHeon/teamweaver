/** 본문에 [rv:…] 출처 표시가 있는지(K5). 있으면 그 뜻("관련 출처", 문장 전체 검증 아님)을 안내한다. */
export function hasMarkers(texts: string[]): boolean {
  return texts.some((t) => /\[rv:[^\]]+\]/.test(t));
}

/** 본문의 [rv:…] 표시를 아래 근거 목록 번호로 바꾼다(화면 친절하게 2026-10-20 -- 내부 ID가 그대로 보이지 않게). 목록에 없는 표시는 "동료 평가". */
export function numberMarkers(text: string, evidence: { source_id: string }[]): string {
  return text.replace(/\s*\[(rv:[^\]]+)\]/g, (_m, id: string) => {
    const k = evidence.findIndex((e) => e.source_id === id);
    return k >= 0 ? ` [근거 ${k + 1}]` : " [동료 평가]";
  });
}
