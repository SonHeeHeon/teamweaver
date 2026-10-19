import { describe, expect, it } from "vitest";
import { mentionsPeople, placedText, withPeople } from "./headcount";

describe("부서장 점검 작은 것(2026-10-19)", () => {
  it("머리줄: 데이터 이름에 이미 인원이 있으면 다시 붙이지 않는다(f2)", () => {
    expect(withPeople("2026년 10월 연간 편성(12개월, 백지) · 100명", 100)).toBe("2026년 10월 연간 편성(12개월, 백지) · 100명");
    expect(withPeople("org-n100-s2026-yearend", 100)).toBe("org-n100-s2026-yearend · 100명");
    expect(withPeople("1100명 조직", 100)).toBe("1100명 조직 · 100명");       // 다른 숫자의 일부는 인원이 아니다
    expect(mentionsPeople("2026년 10월 연말 운영 중 편성 · 100명", 100)).toBe(true);
    expect(mentionsPeople("org-n100-s2026-yearend", 100)).toBe(false);
  });

  it("PDF 배치 인원: 사람 수, 배치가 더 많으면 건수도(f3)", () => {
    expect(placedText([{ person_id: "A" }, { person_id: "B" }])).toBe("2명");
    expect(placedText([{ person_id: "A" }, { person_id: "A" }, { person_id: "B" }])).toBe("2명 · 3건");
    expect(placedText([])).toBe("0명");
  });
});
