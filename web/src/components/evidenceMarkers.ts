/** 본문에 [rv:…] 출처 표시가 있는지(K5). 있으면 그 뜻("관련 출처", 문장 전체 검증 아님)을 안내한다. */
export function hasMarkers(texts: string[]): boolean {
  return texts.some((t) => /\[rv:[^\]]+\]/.test(t));
}
