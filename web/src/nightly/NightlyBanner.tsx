import { useEffect, useState } from "react";
import { fetchNightly, type NightlyStatus } from "./nightlyApi";

/** 아침 알림(claude-b 요청 (1)): 첫 화면 위 띠 "어젯밤 점검: …". 확인할 것이 있으면 펼친다. 돌고 있으면 "밤 배치 진행 중". */
export function NightlyBanner({ refreshKey = "" }: { refreshKey?: string }) {
  const [s, setS] = useState<NightlyStatus | null>(null);
  const [open, setOpen] = useState(false);
  const [hidden, setHidden] = useState(false);
  useEffect(() => { fetchNightly().then(setS).catch(() => setS(null)); }, [refreshKey]);
  if (!s || hidden) return null;
  if (s.running) {
    return (
      <p role="status" className="mb-4 rounded-md border border-pine-200 bg-pine-50 px-4 py-2 text-[13px] text-pine-800">
        밤 배치 진행 중{s.progress?.phase ? ` — ${s.progress.phase}` : ""}. 결과는 끝나면 "밤사이 계산"으로 보입니다.
      </p>
    );
  }
  const last = s.last;
  if (!last?.summary) return null;
  const warn = last.status === "attention";
  const when = last.finished_at ? new Date(last.finished_at).toLocaleString("ko-KR", { dateStyle: "short", timeStyle: "short" }) : "";
  return (
    <div role="status" className={`mb-4 rounded-md border px-4 py-2 text-[13px] ${warn
      ? "border-amber-300 bg-amber-50 text-amber-900" : "border-line bg-paper text-slate-700"}`}>
      <div className="flex flex-wrap items-center gap-2">
        <span>어젯밤 점검{when ? `(${when})` : ""}: <b>{last.summary}</b></span>
        {warn && (last.attention?.length ?? 0) > 0 && (
          <button type="button" className="text-[12px] underline" onClick={() => setOpen((o) => !o)}>
            {open ? "접기" : `확인할 것 ${last.attention!.length}건 보기`}
          </button>
        )}
        <button type="button" className="ml-auto text-[12px] text-slate-500 underline" onClick={() => setHidden(true)}>닫기</button>
      </div>
      {open && (
        <ul className="mt-1.5 list-disc space-y-0.5 pl-5 text-[12.5px]">
          {last.attention!.map((a, i) => <li key={i}><b>{a.title ?? "-"}</b>{a.detail ? ` — ${a.detail}` : ""}</li>)}
        </ul>
      )}
    </div>
  );
}
