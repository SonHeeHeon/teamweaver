/** 후보 표 아래 한 줄 설명(2026-10-19) -- 나눈 두 칸이 무엇인지. 글 규칙은 scoreSplit.ts. */
export function ScoreSplitNote() {
  return (
    <p className="mt-1.5 text-xs text-slate-500">
      빈자리 = 이 사람을 넣으면 메워지거나 새로 생기는 정원 자리(한 자리가 계산 점수 100점이라 따로 보입니다) ·
      기술·협업 점수 = 그 밖의 변화(기술 적합·협업 시너지·익숙한 쌍 감점). 순서는 둘을 더한 값으로 정합니다.
    </p>
  );
}
