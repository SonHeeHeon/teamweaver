import { useState } from "react";
import type { PlacementSettings, SettingsResponse } from "../api/types";
import { FIELDS, JUDGE_LABEL, MODE_LABEL, WINDOW_OPTIONS, toInput, type NumKey } from "./settingsFields";

type Key = NumKey;
type Mode = PlacementSettings["allocation_mode"];
type Judge = "rule" | "jev";

function parseField(f: (typeof FIELDS)[number], raw: string,
                    b: { min: number; max: number } | undefined): number | string {
  if (raw.trim() === "") return "값을 입력해야 한다";
  // Number()는 "0x10"·"1e1"도 받아들인다 -- 평범한 십진수만 허용한다.
  if (!/^\d+(\.\d+)?$/.test(raw.trim())) return "숫자가 아니다";
  if (f.percent && /\.\d{3,}$/.test(raw.trim())) return "소수 둘째 자리까지만 입력할 수 있다";
  const n = Number(raw);
  if (f.integer && !Number.isInteger(n)) return "정수여야 한다";
  const v = f.percent ? n / 100 : n;
  // 부동소수 비교 오차(30/100 등)로 경계값이 거절되지 않게 아주 작은 여유를 둔다.
  if (b && (v < b.min - 1e-9 || v > b.max + 1e-9)) {
    return `${toInput(f, b.min)}~${toInput(f, b.max)}${f.unit} 범위여야 한다`;
  }
  return f.percent ? Math.round(v * 10000) / 10000 : v;
}

interface Props {
  data: SettingsResponse;
  /** 저장 응답을 돌려주면 판정 방식 전환 결과(Jev 실패 등)를 메시지에 반영한다. */
  onSave: (s: PlacementSettings, opts?: { retryJudge?: boolean }) => Promise<SettingsResponse | void>;
  /** 지금 데이터가 실제로 쓰는 판정 방식. 설정은 Jev인데 규칙 기반이면(판정 실패) "판정 다시 시도"를 보인다. */
  activeJudge?: "rule" | "jev";
}

export function SettingsTab({ data, onSave, activeJudge }: Props) {
  const fromSettings = (s: PlacementSettings) =>
    Object.fromEntries(FIELDS.map((f) => [f.key, toInput(f, s[f.key])])) as Record<Key, string>;
  const [form, setForm] = useState<Record<Key, string>>(() => fromSettings(data.settings));
  const [mode, setMode] = useState<Mode>(data.settings.allocation_mode ?? "fixed");
  const savedWindow = data.settings.clique_window_months ?? null;
  const [windowMonths, setWindowMonths] = useState<number | null>(savedWindow);
  const [auto, setAuto] = useState<boolean>(data.settings.time_limit_auto !== false);
  const savedJudge: Judge = data.settings.review_judge ?? "rule";
  const [judge, setJudge] = useState<Judge>(savedJudge);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  // 서버 값이 바뀌면(다른 사람 저장·실행 시 재조회·내 저장) 폼을 새 값으로 다시 채운다.
  // 옛 폼 값이 남으면 손대지 않은 필드까지 예전 값으로 되돌려 저장하게 된다.
  // (렌더 중 이전 prop과 비교해 상태를 맞추는 React 권장 패턴 -- effect보다 한 번 덜 그린다.)
  const [seenUpdatedAt, setSeenUpdatedAt] = useState(data.updated_at);
  if (seenUpdatedAt !== data.updated_at) {
    setSeenUpdatedAt(data.updated_at);
    setForm(fromSettings(data.settings));
    setMode(data.settings.allocation_mode ?? "fixed");
    setWindowMonths(data.settings.clique_window_months ?? null);
    setAuto(data.settings.time_limit_auto !== false);
    setJudge(data.settings.review_judge ?? "rule");
  }

  const hint = mode === "monthly" ? (data.recommended_time_monthly ?? data.recommended_time) : data.recommended_time;
  const parsed = FIELDS.map((f) => [f.key, parseField(f, form[f.key], data.bounds[f.key])] as const);
  const errors = Object.fromEntries(parsed.filter(([, v]) => typeof v === "string"));
  const valid = Object.keys(errors).length === 0;
  const next = valid
    ? ({ ...Object.fromEntries(parsed), allocation_mode: mode, time_limit_auto: auto,
        review_judge: judge, clique_window_months: windowMonths } as unknown as PlacementSettings)
    : null;
  const dirty = next !== null && (FIELDS.some((f) => next[f.key] !== data.settings[f.key])
                                  || mode !== (data.settings.allocation_mode ?? "fixed")
                                  || windowMonths !== savedWindow
                                  || auto !== (data.settings.time_limit_auto !== false)
                                  || judge !== savedJudge);
  const judgeChanging = judge !== savedJudge;
  // 설정은 Jev인데 데이터는 규칙 기반이다(Jev 판정 실패). 저장 버튼은 바뀐 게 없으면 꺼져 있으므로 따로 둔다.
  const judgeFallback = savedJudge === "jev" && judge === "jev" && activeJudge !== undefined && activeJudge !== "jev";
  const [retrying, setRetrying] = useState(false);

  async function save(retryJudge = false) {
    if (!next) return;
    setSaving(true);
    setRetrying(retryJudge);
    setMessage(null);
    try {
      const saved = await (retryJudge ? onSave(next, { retryJudge: true }) : onSave(next));
      const judgeError = saved && saved.dataset?.judge_error;
      setMessage(judgeError
        ? `설정은 저장했지만 ${judgeError} -- '판정 다시 시도'로 다시 판정할 수 있다.`
        : saved && saved.dataset
          ? "저장했다. 새 판정 방식으로 데이터를 다시 만들었다 -- 이전 계산 결과는 비웠으니 다시 실행할 것."
          : "저장했다. 다음 '최적화 실행'부터 이 설정으로 계산한다.");
    } catch (e) {
      setMessage(String(e));
    } finally {
      setSaving(false);
      setRetrying(false);
    }
  }

  return (
    <section className="max-w-2xl">
      <h2 className="mb-1 text-lg font-semibold text-slate-900">배치 설정</h2>
      <p className="mb-4 text-sm text-slate-500">
        조직 공통 배치 규칙이다. 서버에 저장되며, 각 화면은 '최적화 실행'을 누를 때마다
        최신 설정을 다시 읽어 계산한다. 이미 계산한 결과는 그때의 설정을 그대로 쓴다.
      </p>
      {data.load_error && (
        <p role="alert" className="mb-4 rounded-md border border-amber-400 bg-amber-50 px-3 py-2
                                   text-sm text-amber-800">
          {data.load_error}
        </p>
      )}
      <div className="space-y-4 rounded-lg border border-slate-200 bg-white p-4">
        <fieldset>
          <legend className="block text-sm font-medium text-slate-800">투입률 방식</legend>
          <div className="mt-1 flex flex-wrap gap-4 text-sm">
            {(["fixed", "monthly"] as Mode[]).map((m) => (
              <label key={m} className="flex items-center gap-1.5">
                <input type="radio" name="allocation_mode" value={m} checked={mode === m}
                       onChange={() => setMode(m)} />
                {MODE_LABEL[m]}
              </label>
            ))}
          </div>
          <p className="mt-1 text-xs text-slate-500">
            달마다 따로: 다른 프로젝트가 끝나 한가해진 달에 더 많이 배치할 수 있다. 가상 조직 데이터 측정(Plan A)에서 끝까지
            풀면 100명 +20.5%, 200명 +15.4%, 300명 +15.0%였지만 시간이 2~4배 더 든다(300명 약 5분). 시간이 모자라면 오히려
            낮을 수 있으니(300명 180초: −1.6%) 아래 계산 시간을 권장값 이상으로 둔다. 교체 검토·PDF는 가용률·예산을 달별로 확인한다.
          </p>
        </fieldset>
        <fieldset>
          <legend className="block text-sm font-medium text-slate-800">반복 협업 조회 기간</legend>
          <div className="mt-1 flex flex-wrap gap-4 text-sm">
            {[...WINDOW_OPTIONS,
              ...(WINDOW_OPTIONS.some((o) => o.value === savedWindow) ? []
                  : [{ value: savedWindow, label: `최근 ${savedWindow}개월(저장값)` }])].map((o) => (
              <label key={String(o.value)} className="flex items-center gap-1.5">
                <input type="radio" name="clique_window_months" value={String(o.value)}
                       checked={windowMonths === o.value} onChange={() => setWindowMonths(o.value)} />
                {o.label}
              </label>
            ))}
          </div>
          <p className="mt-1 text-xs text-slate-500">
            이 기간 안에서 아래 '반복 협업 기준' 개월 이상 함께 일한 두 사람을 같은 프로젝트에 넣으면 감점한다. 협업 점수(시너지)는
            이 값과 상관없이 최근 10년 이력을 쓴다. 가상 조직 데이터 비교(현행 10년 중 6개월 · 5년 중 12개월 · 3년 중 12개월)에서
            '최근 3년 중 12개월'만 100·200·300명 모두 같은 시간 안에 빈자리 없이 안정적으로 풀렸다(2026-10-06 결정).
            함께 일한 달 정보가 없는 데이터(예전 고정 데모 데이터)는 기간을 적용할 수 없어 전체 이력으로 센다.
          </p>
        </fieldset>
        <fieldset>
          <legend className="block text-sm font-medium text-slate-800">리뷰 글 판정 방식</legend>
          <div className="mt-1 flex flex-wrap gap-4 text-sm">
            {(["rule", "jev"] as Judge[]).map((j) => (
              <label key={j} className="flex items-center gap-1.5">
                <input type="radio" name="review_judge" value={j} checked={judge === j}
                       disabled={j === "jev" && !data.jev_available && savedJudge !== "jev"}
                       onChange={() => setJudge(j)} />
                {JUDGE_LABEL[j]}
              </label>
            ))}
          </div>
          <p className="mt-1 text-xs text-slate-500">
            협업 점수의 동료 평가 부분을 무엇으로 매길지 고른다. 규칙 기반은 좋은 점·아쉬운 점 항목 수로 계산하고 아무것도
            밖으로 보내지 않는다. Jev는 평가 글을 읽고 판정한다. 어느 평가가 더 긍정적인지의 순서는 규칙 기반과 대체로 같지만
            (시연 데이터 100명 실측 상관 0.88), 모든 평가를 더 긍정적으로 매긴다(평균 0.56 대 0.16, 부정 판정 없음). 그래서
            협업 점수가 전반적으로 오르고 사이가 나쁜 쌍을 피하는 힘이 약해진다. 판정 방식이 다른 계산 결과끼리 지표를
            비교하지 않는다. 바꾸면 서버가 지금 데이터를 새 방식으로 다시 만든다(Jev 첫 판정은 100명 기준 약 20초).
          </p>
          {!data.jev_available && (
            <p className="mt-1 text-xs text-slate-500">서버에 Jev 키(TYPESAFE_API_KEY)가 없어 Jev를 고를 수 없다.</p>
          )}
          {judge === "jev" && (
            <p role="alert" className="mt-2 rounded-md border border-amber-400 bg-amber-50 px-3 py-2 text-xs text-amber-900">
              Jev를 쓰면 <b>동료 평가 원문이 외부(TypeSafe Jev API)로 전송된다.</b> 평가 글을 회사 밖으로 보낼 수
              있는 조직만 고른다.
            </p>
          )}
        </fieldset>
        {FIELDS.map((f) => (
          <div key={f.key}>
            <label className="block text-sm font-medium text-slate-800" htmlFor={`set-${f.key}`}>
              {f.label}
              {f.key !== "min_alloc" ? null : (
                <span className="ml-2 text-xs font-normal text-slate-500">
                  (기본 {toInput(f, data.defaults[f.key])}%)
                </span>
              )}
            </label>
            <div className="mt-1 flex items-center gap-2">
              <input
                id={`set-${f.key}`}
                inputMode="decimal"
                disabled={f.key === "time_limit" && auto}
                value={f.key === "time_limit" && auto && hint ? String(hint.per_solve_s) : form[f.key]}
                onChange={(e) => setForm({ ...form, [f.key]: e.target.value })}
                aria-invalid={f.key in errors}
                aria-describedby={`set-${f.key}-help${f.key in errors ? ` set-${f.key}-err` : ""}`}
                className={`w-28 rounded-md border px-2 py-1 text-sm tabular-nums ${
                  f.key in errors ? "border-red-400" : "border-slate-300"}`}
              />
              <span className="text-sm text-slate-500">{f.unit}</span>
              {f.key === "time_limit" && (
                <label className="ml-2 flex items-center gap-1 text-sm text-slate-700">
                  <input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} />
                  자동(인원 기준)
                </label>
              )}
            </div>
            {f.key in errors && (
              <p id={`set-${f.key}-err`} className="mt-1 text-xs text-red-600">
                {errors[f.key] as string}
              </p>
            )}
            <p id={`set-${f.key}-help`} className="mt-1 text-xs text-slate-500">{f.help}</p>
            {f.key === "time_limit" && hint && (
              <p className="mt-1 text-xs text-sky-700">
                지금 데이터({hint.n_people}명, {MODE_LABEL[mode]}) 권장: {hint.per_solve_s}초
                (Plan A와 대안 3개 최악 합계 {hint.worst_case_total_s}초)
                {hint.measured ? "" : " · 측정 범위 밖이라 최소 기준"} — {hint.basis}
              </p>
            )}
          </div>
        ))}
      </div>
      <div className="mt-4 flex flex-wrap items-center gap-3">
        <button
          onClick={() => save()}
          disabled={!valid || !dirty || saving}
          className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white
                     hover:bg-slate-700 disabled:cursor-not-allowed disabled:bg-slate-300"
        >
          {saving && !retrying ? (judgeChanging ? "데이터 다시 만드는 중…" : "저장 중…") : "저장"}
        </button>
        {judgeFallback && (
          <button
            onClick={() => save(true)}
            disabled={!valid || saving}
            className="rounded-md border border-amber-400 bg-amber-50 px-4 py-2 text-sm font-medium text-amber-900
                       hover:bg-amber-100 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {retrying ? "판정 다시 시도 중…" : "판정 다시 시도"}
          </button>
        )}
        <button
          onClick={() => setForm(fromSettings(data.defaults))}
          className="rounded-md border border-slate-300 bg-white px-4 py-2 text-sm font-medium
                     text-slate-700 hover:bg-slate-50"
        >
          기본값으로 채우기
        </button>
        <span className="text-xs text-slate-500">
          {data.updated_at ? `마지막 저장 ${new Date(data.updated_at).toLocaleString("ko-KR")}`
                           : "저장한 적 없음(기본값 사용 중)"}
        </span>
      </div>
      {message && <p className="mt-3 text-sm text-slate-700">{message}</p>}
    </section>
  );
}
