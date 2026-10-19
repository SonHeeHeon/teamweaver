import { useCallback, useEffect, useState } from "react";
import { AdminLoginRequiredError } from "../api/client";
import { fetchNightly, runNightly, stopNightly, type NightlyStatus } from "./nightlyApi";

/** 관리자: 밤 배치 예약 상태와 "지금 한 번 돌리기"·"멈추기"(claude-b 요청 (2)). 밤 배치는 AI를 쓰지 않는다(계산만). */
export function NightlyPanel({ adminToken = null, onLoginRequired }: {
  adminToken?: string | null; onLoginRequired?: (reason: string) => void;
}) {
  const [s, setS] = useState<NightlyStatus | null>(null);
  const [minutes, setMinutes] = useState(30);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => { fetchNightly().then(setS).catch((e) => setMsg((e as Error).message)); }, []);
  useEffect(() => { load(); }, [load]);
  const act = async (fn: () => Promise<string>) => {
    setBusy(true); setMsg(null);
    try { setMsg(await fn()); load(); }
    catch (e) { if (e instanceof AdminLoginRequiredError) onLoginRequired?.(e.message); else setMsg((e as Error).message); }
    finally { setBusy(false); }
  };
  if (!s) return null;
  return (
    <section className="panel max-w-[880px] p-5" aria-labelledby="nightly-title">
      <h2 id="nightly-title" className="text-base font-semibold text-slate-900">밤 배치</h2>
      <p className="mt-1 text-[13px] text-slate-500">
        매일 {s.window}에 지금 데이터로 긴급 대기 명단·자동 편성 등을 시간을 넉넉히 들여 미리 풀어 둡니다(AI 비용 없음).
        {s.enabled ? (s.next_run_at ? ` 다음 실행 ${new Date(s.next_run_at).toLocaleString("ko-KR")}.` : "") : " 지금은 예약이 꺼져 있습니다."}
      </p>
      {s.last?.summary && <p className="mt-2 text-[13px]">마지막 실행: <b>{s.last.summary}</b></p>}
      <div className="mt-3 flex flex-wrap items-center gap-2 text-[13px]">
        {s.running ? (
          <>
            <span className="text-pine-800">진행 중{s.progress?.phase ? ` — ${s.progress.phase}` : ""}</span>
            <button type="button" className="btn" disabled={busy}
                    onClick={() => void act(async () => { await stopNightly(adminToken); return "멈춤을 요청했습니다(풀던 것은 그때까지의 해로 끝냅니다)."; })}>
              멈추기
            </button>
          </>
        ) : (
          <>
            <label>최대{" "}
              <input aria-label="밤 배치 최대 시간(분)" type="number" min={2} max={300} className="field inline-block w-20 py-0.5"
                     value={minutes} onChange={(e) => setMinutes(Math.max(2, Math.min(300, Number(e.target.value) || 2)))} />
              {" "}분
            </label>
            <button type="button" className="btn-primary" disabled={busy}
                    onClick={() => void act(async () => (await runNightly(minutes, adminToken))
                      ? "시작했습니다. 끝나면 결과가 \"밤사이 계산\"으로 보입니다." : "이미 돌고 있습니다.")}>
              지금 한 번 돌리기
            </button>
          </>
        )}
        <button type="button" className="btn-ghost" onClick={load}>새로 고침</button>
      </div>
      {msg && <p className="mt-2 text-[12.5px] text-slate-600">{msg}</p>}
    </section>
  );
}
