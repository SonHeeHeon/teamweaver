import type { PlanEvent } from "./types";

export type SseEvent =
  | { event: "plan"; data: PlanEvent }
  | { event: "done"; data: { count: number } }
  | { event: "error"; data: { message: string } };

/** 버퍼에서 완성된 SSE 프레임(빈 줄로 구분)만 뽑고 나머지는 돌려준다.
 *
 *  네트워크 청크 경계는 프레임 경계와 아무 관계가 없다 -- 청크 하나가 프레임
 *  한가운데를 자를 수 있으므로, 미완성 꼬리는 반드시 버퍼에 남겨 다음 청크와
 *  이어 붙여야 한다. 이걸 놓치면 플랜 이벤트가 조용히 사라진다. */
export function parseFrames(buffer: string): { events: SseEvent[]; rest: string } {
  const events: SseEvent[] = [];
  let rest = buffer;
  for (;;) {
    const idx = rest.indexOf("\n\n");
    if (idx === -1) break;
    const frame = rest.slice(0, idx);
    rest = rest.slice(idx + 2);

    let name = "message";
    const dataLines: string[] = [];
    for (const line of frame.split("\n")) {
      if (line.startsWith("event:")) name = line.slice(6).trim();
      else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
    }
    if (dataLines.length === 0) continue;   // 주석(: ...)이나 keep-alive
    events.push({ event: name, data: JSON.parse(dataLines.join("\n")) } as SseEvent);
  }
  return { events, rest };
}
