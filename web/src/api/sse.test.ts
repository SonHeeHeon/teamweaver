import { describe, expect, it } from "vitest";
import { parseFrames, type SseEvent } from "./sse";

const isPlan = (e: SseEvent): e is Extract<SseEvent, { event: "plan" }> =>
  e.event === "plan";

describe("parseFrames", () => {
  it("완성된 프레임만 뽑고 나머지는 버퍼에 남긴다", () => {
    const buf = 'event: plan\ndata: {"label":"A"}\n\nevent: done\ndata: {"co';
    const { events, rest } = parseFrames(buf);
    expect(events).toEqual([{ event: "plan", data: { label: "A" } }]);
    expect(rest).toBe('event: done\ndata: {"co');
  });

  it("한 번에 여러 프레임이 와도 순서대로 뽑는다", () => {
    const buf =
      'event: plan\ndata: {"label":"A"}\n\nevent: plan\ndata: {"label":"B"}\n\n';
    const { events, rest } = parseFrames(buf);
    expect(events.filter(isPlan).map((e) => e.data.label)).toEqual(["A", "B"]);
    expect(rest).toBe("");
  });

  it("네트워크 경계가 프레임 한가운데를 갈라도 잃지 않는다", () => {
    // 실제 스트림에서 청크 경계는 프레임 경계와 무관하다 -- 이걸 못 버티면
    // 플랜 이벤트가 조용히 사라진다.
    const chunk1 = 'event: plan\ndata: {"lab';
    const chunk2 = 'el":"A"}\n\n';
    const first = parseFrames(chunk1);
    expect(first.events).toEqual([]);
    const second = parseFrames(first.rest + chunk2);
    expect(second.events).toEqual([{ event: "plan", data: { label: "A" } }]);
  });

  it("data가 없는 프레임은 무시한다", () => {
    const { events } = parseFrames(": keep-alive\n\n");
    expect(events).toEqual([]);
  });
});
