import type { WhatifResponse } from "./types";

/** 화면과 PDF가 같은 문장으로 경고하도록 What-if 결과의 문제를 한 목록으로 만든다. */
export function swapWarnings(res: WhatifResponse): string[] {
  const out = res.new_violations.map((v) => v.message);
  out.push(...res.new_shortfalls.map((s) => `${s.project_id}의 ${s.grade} ${s.missing}명 미충원`));
  if (!res.feasible && res.new_violations.length === 0) {
    out.push("교체 전 배치에도 제약 위반이 있다");
  }
  return out;
}
