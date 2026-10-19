/** 사람 수 표기 도우미(2026-10-19 부서장 점검). 화면 부품 파일 밖에 둔다(fast refresh 린트). */

/** 머리줄의 "데이터 이름 · N명": 이름(편성 기록 제목)에 이미 "N명"이 있으면 다시 붙이지 않는다
 *  (점검 f2 -- "2026년 10월 연간 편성(12개월, 백지) · 100명 · 100명"). */
export function withPeople(label: string, people: number): string {
  return mentionsPeople(label, people) ? label : `${label} · ${people}명`;
}

/** 이름에 "N명"이 이미 있나(다른 숫자의 일부는 아님) */
export function mentionsPeople(label: string, people: number): boolean {
  return new RegExp(`(^|[^0-9])${people}명`).test(label);
}

/** PDF 배치 인원 칸: 사람 수, 배치 건수가 더 많으면 건수도(점검 f3 -- 12개월 계획은 한 사람이 사업을 차례로 맡아
 *  "배치 인원 134명"이 사람 수처럼 읽혔다. 100명 · 134건). */
export function placedText(entries: { person_id: string }[]): string {
  const people = new Set(entries.map((e) => e.person_id)).size;
  return entries.length > people ? `${people}명 · ${entries.length}건` : `${people}명`;
}
