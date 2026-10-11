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
  if (reason === "addendum_check") return "덧붙일 불리한 사실의 주인을 팀에서 찾지 못해 정해진 틀로 썼습니다.";
  if (reason === "render_check") return "AI 글에 사실을 채운 결과 확인에 실패해 정해진 틀로 썼습니다.";
  return `정해진 틀로 썼습니다(${reason}).`;
}



export type Segment = { text: string } | { cite: string; label: string | null };

/** 글을 일반 글과 근거 조각으로 나눈다(사용자 결정 2026-10-11: 근거 번호 [F12]은 숨기고 근거 문구를 누르게).
 *  서버는 사실 문구를 채운 바로 뒤에 [F#]을 붙인다 -- 그 앞 글이 사실의 phrase_full·phrase로 끝나면 그 부분을 근거 조각으로 만든다.
 *  못 찾으면(문구가 다르게 채워진 경우) 이름 없는 근거 조각(작은 표시)으로 둔다. */
export function segmentsOf(text: string,
                           facts: { id: string; phrase?: string; phrase_full?: string; text?: string }[]): Segment[] {
  const byId = new Map(facts.map((f) => [f.id, f]));
  const out: Segment[] = [];
  for (const part of text.split(/(\[F\d+\])/g)) {
    const m = /^\[(F\d+)\]$/.exec(part);
    if (!m) { if (part) out.push({ text: part }); continue; }
    const f = byId.get(m[1]);
    const prev = out[out.length - 1];
    // AI 글은 phrase_full·phrase를 채운 바로 뒤, 정해진 틀 글은 "사실 한 줄(text). " 뒤에 번호가 붙는다
    const candidates = [f?.phrase_full, f?.phrase, f?.text].filter((c): c is string => !!c)
      .sort((a, b) => b.length - a.length);
    let done = false;
    if (prev && "text" in prev) {
      const whole = prev.text;
      const trimmed = whole.replace(/[.\s]+$/, "");
      const suffix = whole.slice(trimmed.length);            // 문구 뒤 마침표·띄어쓰기(정해진 틀)
      for (const c of candidates) {
        const base = whole.endsWith(c) ? whole : trimmed.endsWith(c) ? trimmed : null;
        if (base === null) continue;
        const rest = base.slice(0, -c.length);
        if (rest) prev.text = rest; else out.pop();
        out.push({ cite: m[1], label: c });
        if (base === trimmed && suffix) out.push({ text: suffix });
        done = true;
        break;
      }
    }
    if (!done) out.push({ cite: m[1], label: null });
  }
  return out;
}
