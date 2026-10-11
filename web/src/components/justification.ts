/** 인사팀 소명 글 화면·PDF 공용 표기(claude-a 계약 docs/requests/2026-10-09-hr-justification.md). */
export const KIND_LABEL: Record<string, string> = {
  PRJ: "사업", REQ: "요구 기술", MEM: "팀원", SKL: "사람별 기술", IND: "같은 산업 경험", CLI: "같은 고객사 경험",
  CW: "함께 일한 이력", REV: "동료 평가", ALT: "다른 후보와 비교", CON: "규칙 준수",
};
export const KIND_ORDER = ["PRJ", "REQ", "MEM", "SKL", "IND", "CLI", "CW", "REV", "ALT", "CON"];

const RULE_TEXT: Record<string, string> = {
  S1: "없는 근거 번호를 써서", S2: "정해진 표현 밖의 말이 있어", S3: "팀원이 아니거나 지어낸 ID가 있어",
  S4: "사실이 다른 사람·다른 주제 아래 놓여", S5: "빠진 팀원이 있어", S6: "빈 글이라",
};

/** 정해진 틀로 쓴 이유를 사람 말로. */
export function fallbackText(reason: string | null): string | null {
  if (!reason) return null;
  if (reason === "no_client") return "AI 키가 없어 정해진 틀로 썼습니다.";
  if (reason === "busy") return "다른 AI 글이 많이 계산 중이라 정해진 틀로 썼습니다. 잠시 뒤 '다시 쓰기'를 누르세요.";
  if (reason === "external_blocked") return "실데이터라 외부 AI로 보내지 않고 정해진 틀로 썼습니다.";
  if (reason.startsWith("verify:")) {
    const why = reason.slice(7).split(",").map((r) => RULE_TEXT[r] ?? r).join(", ");
    return `${why} AI 글을 쓰지 않았습니다(정해진 틀로 대신).`;
  }
  if (reason.startsWith("llm_error")) return "AI 호출이 실패해 정해진 틀로 썼습니다.";
  if (reason.startsWith("llm_bad_json")) return "AI 응답 형식이 맞지 않아 정해진 틀로 썼습니다.";
  if (reason.startsWith("verify_error")) return "AI 글 검사 중 오류가 나서 정해진 틀로 썼습니다.";
  if (reason === "render_check") return "AI 글에 사실을 채운 결과 확인에 실패해 정해진 틀로 썼습니다.";
  return `정해진 틀로 썼습니다(${reason}).`;
}

