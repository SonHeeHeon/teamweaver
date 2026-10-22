/** 신규 사업 등록 양식의 기본값과 단계별 검사(서버 NewProject와 같은 규칙의 일부). */
import type { ProjectForm as Form } from "./workspaceApi";

export function emptyForm(start: string | null | undefined): Form {
  return { name: "", sector: "대외금융", start_month: start ?? "", end_month: start ?? "", monthly_budget: 5000,
           stage: "예정", predecessor: null, parent: null, external_headcount: 0, grades: { 특급: 0, 고급: 1, 중급: 1, 초급: 0 }, skills: [] };
}

/** 단계별로 막을 것(서버와 같은 규칙의 일부). 빈 배열이면 다음으로. */
export function formProblems(f: Form, step: number): string[] {
  const out: string[] = [];
  if (step >= 0) {
    if (!f.name.trim()) out.push("사업명을 적는다");
    if (!/^\d{4}-\d{2}$/.test(f.start_month) || !/^\d{4}-\d{2}$/.test(f.end_month)) out.push("시작·종료 달을 고른다");
    else if (f.end_month < f.start_month) out.push("종료 달이 시작 달보다 앞이다");
    if (!(f.monthly_budget > 0)) out.push("월 예산은 0보다 커야 한다");
  }
  if (step >= 2 && Object.values(f.grades).reduce((a, b) => a + (b ?? 0), 0) < 1) out.push("등급별 인원이 한 명 이상이어야 한다");
  if (step >= 3) {
    const names = f.skills.map((s) => s.name.trim().toLowerCase());
    if (new Set(names).size !== names.length) out.push("같은 기술이 두 번 들어 있다");
    // 서버(입력 검증)는 기술 요구가 없는 사업을 받지 않는다 -- 등록 끝에서 "검증을 통과하지 못했다"로만 알리던 것을 여기서 막는다(2026-10-23 화면 확인)
    if (names.length === 0) out.push("필요 기술을 하나 이상 고른다(기술 점수 계산에 필요)");
  }
  return out;
}

/** 네 등급을 모두 숫자로 -- 서버는 "적힌 0 = 그 등급 금지", "빠진 등급 = 제한 없음"으로 읽으므로, 화면에 0으로 보인 등급은 0으로 보낸다
 *  (Codex 리뷰 2026-10-19). */
export function normalizeGrades(g: Form["grades"]): Form["grades"] {
  return { 특급: g.특급 ?? 0, 고급: g.고급 ?? 0, 중급: g.중급 ?? 0, 초급: g.초급 ?? 0 };
}
