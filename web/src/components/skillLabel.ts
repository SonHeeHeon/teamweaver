import type { Person } from "../api/types";

/** 사람의 기술을 레벨 높은 순으로, 하위 기술에서 인정된 것은 출처와 함께(사용자 결정 2026-10-11 "구분해서 표시"). */
export function skillChips(p: Person | undefined, limit = 8): { skill: string; level: number; credit: string | null }[] {
  if (!p) return [];
  return Object.entries(p.skills)
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .slice(0, limit)
    .map(([skill, level]) => {
      const c = p.skill_credit?.[skill];
      return { skill, level, credit: c ? `${c.source} 경력에서 인정` : null };
    });
}

/** 실무 수준(12개월+, 입력 단계 레벨 경계 첫 칸)이 된 인정만 -- 몇 개월짜리 인정까지 다 적으면 거의 모든 사람에게 붙어 읽기 어렵다(시연 100명 중 98명). */
export const PRACTICAL_MONTHS = 12;

export function practicalCredits(p: Person | undefined) {
  return Object.entries(p?.skill_credit ?? {}).filter(([, c]) => c.months >= PRACTICAL_MONTHS);
}

/** 실무 수준 인정만 한 줄로(PDF용). 예: "Java 18개월(Spring 경력에서 인정)". */
export function creditedLine(p: Person | undefined): string {
  return practicalCredits(p).map(([skill, c]) => `${skill} ${c.months}개월(${c.source} 경력에서 인정)`).join(", ");
}
