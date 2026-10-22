import type { WhatifResponse } from "./types";

/** 등급이 바뀐 교체(연간 계획 ② -- 등급 대체를 적용하지 않고 평가한다)를 확정 경로와 같은 말 "등급 대체"로 묶는다(2026-10-22 화면 검토 P3-1).
 *  같은 사업의 "○급 배치 초과"(grade_over)와 새 "○급 미충원"을 짝지어 한 줄로 -- 이 안을 확정하면 사유를 받고 정원을 바꾸는 일이라
 *  규칙 위반으로 세지 않는다. 짝이 안 맞는 몫은 원래 문장 그대로 남긴다. */
export function gradeSwapLines(res: WhatifResponse): { lines: string[]; violations: Set<string>; shortfalls: Set<string> } {
  const lines: string[] = [];
  const violations = new Set<string>(), shortfalls = new Set<string>();
  for (const v of res.new_violations) {
    if (v.code !== "grade_over") continue;
    const [jid, grade] = v.location.split(":");
    let over = Math.round(v.actual - v.limit);
    for (const s of res.new_shortfalls) {
      const key = `${s.project_id}:${s.grade}`;
      if (over <= 0 || s.project_id !== jid || s.grade === grade || shortfalls.has(key)) continue;
      const k = Math.min(over, s.missing);
      lines.push(`등급 대체 — ${jid}의 ${s.grade} 자리 ${k}개를 ${grade}으로 채운다(이 안을 확정할 때 사유를 받고 그 사업 정원이 바뀐다)`);
      over -= k;
      if (k >= s.missing) shortfalls.add(key);
      violations.add(`${v.code}@${v.location}`);
    }
  }
  return { lines, violations, shortfalls };
}

/** 등급 대체로 묶은 것을 뺀 진짜 새 위반(빨간 "교체 시 제약 위반" 표시용). */
export function blockingViolations(res: WhatifResponse) {
  const { violations } = gradeSwapLines(res);
  return res.new_violations.filter((v) => !violations.has(`${v.code}@${v.location}`));
}

/** 화면과 PDF가 같은 문장으로 경고하도록 What-if 결과의 문제를 한 목록으로 만든다. */
export function swapWarnings(res: WhatifResponse): string[] {
  const swap = gradeSwapLines(res);
  const out = [...swap.lines, ...blockingViolations(res).map((v) => v.message)];
  out.push(...res.new_shortfalls.filter((s) => !swap.shortfalls.has(`${s.project_id}:${s.grade}`))
    .map((s) => `${s.project_id}의 ${s.grade} ${s.missing}명 미충원`));
  if (!res.feasible && res.new_violations.length === 0) {
    out.push("교체 전 배치에도 제약 위반이 있다");
  }
  return out;
}
