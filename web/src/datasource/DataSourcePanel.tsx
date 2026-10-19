import { useCallback, useEffect, useState } from "react";
import { AdminLoginRequiredError } from "../api/client";
import type { DatasetInfo } from "../api/types";
import { fetchHrSource, setOwner, syncNow, type HrSourceStatus, type Owner } from "./hrSourceApi";

const TABLE_LABEL: Record<string, string> = {
  "people.csv": "인력", "rate_card.csv": "단가", "person_skills.csv": "기술 경력", "work_history.csv": "업무 이력",
  "work_skills.csv": "업무별 사용 기술", "availability.csv": "가용률", "reviews.csv": "동료 평가",
  "review_items.csv": "평가 항목", "project_outcomes.csv": "과거 성과", "replacements.csv": "교체 기록",
  "projects.csv": "사업", "project_grade_requirements.csv": "등급 정원", "project_skill_requirements.csv": "요구 기술",
  "current_assignments.csv": "현재 배치",
};
const RESULT_LABEL: Record<string, string> = {
  applied: "새 판으로 켰다", unchanged: "원천이 그대로라 새 판 없음", invalid: "검사를 통과하지 못해 지금 데이터 그대로",
  fail: "받지 못해 지금 데이터 그대로", off: "연결 설정 없음",
};

/** 관리자: 데이터 원천(인사 시스템 API가 기본, 없으면 CSV)과 진행 중 사업 기술 경력 갱신 주체(사용자 요청 2026-10-19). */
export function DataSourcePanel({ adminToken = null, datasetVersion, onSwitched, onExternalChange, onLoginRequired,
                                  pollMs = 3000 }: {
  adminToken?: string | null; datasetVersion: string | null;
  onSwitched: (info: DatasetInfo) => void;
  /** 다른 곳(밤 배치·다른 관리자)의 받기가 새 판을 켰다 -- 화면이 데이터를 다시 읽는다 */
  onExternalChange?: () => void;
  onLoginRequired?: (reason: string) => void;
  pollMs?: number;
}) {
  const [s, setS] = useState<HrSourceStatus | null>(null);
  const [owner, setOwnerChoice] = useState<Owner>("service");
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const show = useCallback((st: HrSourceStatus | null) => {
    setS(st);
    if (st?.skill_months_owner) setOwnerChoice(st.skill_months_owner);
  }, []);
  const load = useCallback(() => { fetchHrSource().then(show).catch((e) => setMsg((e as Error).message)); }, [show]);
  useEffect(() => { load(); }, [load, datasetVersion]);
  // 받는 중이면(밤 배치·다른 관리자) 끝날 때까지 다시 읽는다 -- 끝나서 새 판이 켜졌으면 화면 데이터를 바꾼다(Codex 재리뷰)
  useEffect(() => {
    if (!s?.running) return;
    const id = setTimeout(() => {
      fetchHrSource().then((st) => {
        show(st);
        if (st && !st.running && st.last?.status === "applied" && st.last.version && st.last.version !== datasetVersion) {
          onExternalChange?.();
        }
      }).catch((e) => setMsg((e as Error).message));
    }, pollMs);
    return () => clearTimeout(id);
  }, [s, datasetVersion, onExternalChange, pollMs, show]);
  const act = async (fn: () => Promise<string>) => {
    setBusy(true); setMsg(null);
    try { setMsg(await fn()); }
    catch (e) { if (e instanceof AdminLoginRequiredError) onLoginRequired?.(e.message); else setMsg((e as Error).message); }
    finally { setBusy(false); }
  };
  if (!s) return null;
  const fromApi = s.tables.filter((t) => !t.service && t.from === "api").map((t) => TABLE_LABEL[t.name] ?? t.name);
  const fromData = s.tables.filter((t) => !t.service && t.from === "data").map((t) => TABLE_LABEL[t.name] ?? t.name);
  const service = s.tables.filter((t) => t.service).map((t) => `${TABLE_LABEL[t.name] ?? t.name}${t.from === "api" ? "(API)" : ""}`);
  const a = s.aging;
  return (
    <section className="panel max-w-[880px] p-5" aria-labelledby="datasource-title">
      <h2 id="datasource-title" className="text-base font-semibold text-slate-900">데이터 원천</h2>
      {s.mode === "api" ? (
        <p className="mt-1 text-[13px] text-slate-500">
          인사 시스템 API{s.host ? `(${s.host})` : ""}에서 받습니다. 밤 배치 맨 앞에서 매일 받고, 바뀐 것이 있을 때만 업로드와 같은 검사를 거쳐
          새 판으로 켭니다(되돌리기 가능). 검사에 걸리면 지금 데이터를 그대로 씁니다. CSV 묶음 업로드도 그대로 쓸 수 있습니다.
        </p>
      ) : (
        <p className="mt-1 text-[13px] text-slate-500">
          인사 시스템 연결이 설정돼 있지 않아 CSV 묶음(데이터 탭 업로드)으로 받습니다. 기술 이력·동료 평가 서비스가 있는 회사는
          서버에 <code>TEAMWEAVER_HR_API_URL</code>·<code>TEAMWEAVER_HR_API_TOKEN</code>을 두면 매일 자동으로 받습니다.
        </p>
      )}
      {s.config_error && <p role="alert" className="mt-2 text-[13px] text-red-700">{s.config_error}</p>}
      <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-[13px]">
        {fromApi.length > 0 && <><dt className="text-slate-500">인사 시스템</dt><dd>{fromApi.join(" · ")}</dd></>}
        {fromData.length > 0 && (
          <><dt className="text-slate-500">{s.mode === "api" ? "지금 데이터(CSV)" : "CSV 묶음"}</dt><dd>{fromData.join(" · ")}</dd></>
        )}
        <dt className="text-slate-500">이 서비스</dt><dd>{service.join(" · ")} — 화면에서 등록·확정한 것</dd>
      </dl>
      {s.mode === "api" && (
        <div className="mt-3 flex flex-wrap items-center gap-2 text-[13px]">
          <span>
            마지막 받기: {s.last
              ? <><b>{RESULT_LABEL[s.last.status] ?? s.last.status}</b> ({new Date(s.last.at).toLocaleString("ko-KR")}
                  {s.last.trigger === "nightly" ? ", 밤 배치" : ""}){s.last.status !== "applied" && s.last.detail ? ` — ${s.last.detail}` : ""}</>
              : "아직 없음"}
          </span>
          <button type="button" className="btn-primary" disabled={busy || s.running || !!s.config_error}
                  onClick={() => void act(async () => {
                    const r = await syncNow(adminToken);
                    show(r.status);
                    if (r.result.status === "applied") onSwitched(r.dataset);
                    const saved = r.result.status === "applied" && r.result.persisted === false
                      ? ` — 다만 서버에 저장하지 못했다(재기동하면 이전 데이터로 뜬다): ${r.result.persist_error ?? ""}` : "";
                    return `${RESULT_LABEL[r.result.status] ?? r.result.status}${r.result.status === "applied" ? "" : ` — ${r.result.detail}`}${saved}`;
                  })}>
            {s.running ? "받는 중…" : "지금 받기"}
          </button>
        </div>
      )}
      <fieldset className="mt-4">
        <legend className="text-sm font-medium text-slate-800">진행 중 사업의 기술 경력(개월 수) 갱신</legend>
        <div className="mt-1 space-y-1 text-[13px]">
          <label className="flex items-start gap-1.5">
            <input type="radio" name="skill_months_owner" checked={owner === "service"} onChange={() => setOwnerChoice("service")}
                   disabled={!s.editable} />
            <span><b>TeamWeaver가 늘린다</b>(기본) — 기술 이력 서비스가 진행 중 사업의 경력을 등록 때 숫자로 두는 경우. 진행 중 업무에 등록된
              기술만, 각 경력의 마지막 사용 달 뒤부터 계획 직전 달까지 더합니다(이미 센 달은 다시 세지 않습니다).</span>
          </label>
          <label className="flex items-start gap-1.5">
            <input type="radio" name="skill_months_owner" checked={owner === "source"} onChange={() => setOwnerChoice("source")}
                   disabled={!s.editable} />
            <span><b>원천 시스템이 갱신한다</b> — 기술 이력 서비스가 진행 중 사업의 경력을 매달 늘려 주는 경우. 받은 숫자를 그대로 씁니다.</span>
          </label>
        </div>
        {a && (
          <p className="mt-2 text-[12.5px] text-slate-600">
            지금 데이터: 데이터 기준월 {a.as_of ?? "모름"} · 계획 직전 달 {a.cutoff ?? "-"} ·{" "}
            {a.owner === "source" ? "원천 숫자 그대로"
              : a.skipped === "no_work_skills"
                ? <span className={a.as_of && a.cutoff && a.as_of < a.cutoff ? "text-amber-700" : ""}>
                    업무별 사용 기술 표가 없어 늘리지 못함{a.as_of && a.cutoff && a.as_of < a.cutoff
                      ? ` — 데이터 기준월 ${a.as_of} 뒤 ${a.cutoff}까지의 경력이 빠져 있다` : ""}
                  </span>
                : a.skills > 0 ? `늘린 경력 ${a.skills}건(${a.people}명, 최대 +${a.max_months}개월)`
                  : "늘릴 달 없음(원천이 계획 직전 달까지 셌다)"}
          </p>
        )}
        {!s.editable && s.not_editable_reason && <p className="mt-1 text-[12.5px] text-slate-500">{s.not_editable_reason}</p>}
        <button type="button" className="btn mt-2" disabled={busy || !s.editable || !datasetVersion || owner === s.skill_months_owner}
                onClick={() => void act(async () => {
                  const r = await setOwner(datasetVersion as string, owner, adminToken);
                  show(r.status);
                  if (!r.unchanged) onSwitched(r.dataset);
                  if (r.unchanged) return "이미 그 설정입니다.";
                  return r.persisted === false
                    ? `적용했지만 서버에 저장하지 못했다(재기동하면 이전 설정으로 뜬다): ${r.persist_error ?? ""}`
                    : "적용했습니다(새 판 -- 되돌리기 가능). 다음 계산부터 이 경력으로 셉니다.";
                })}>
          적용
        </button>
      </fieldset>
      {msg && <p className="mt-2 text-[12.5px] text-slate-600">{msg}</p>}
    </section>
  );
}
