import { useState } from "react";
import type { PlacementSettings, SettingsResponse } from "../api/types";
import { FIELDS, toInput } from "./settingsFields";

type Key = keyof PlacementSettings;

function parseField(f: (typeof FIELDS)[number], raw: string,
                    b: { min: number; max: number }): number | string {
  if (raw.trim() === "") return "값을 입력해야 한다";
  // Number()는 "0x10"·"1e1"도 받아들인다 -- 평범한 십진수만 허용한다.
  if (!/^\d+(\.\d+)?$/.test(raw.trim())) return "숫자가 아니다";
  if (f.percent && /\.\d{3,}$/.test(raw.trim())) return "소수 둘째 자리까지만 입력할 수 있다";
  const n = Number(raw);
  if (f.integer && !Number.isInteger(n)) return "정수여야 한다";
  const v = f.percent ? n / 100 : n;
  // 부동소수 비교 오차(30/100 등)로 경계값이 거절되지 않게 아주 작은 여유를 둔다.
  if (v < b.min - 1e-9 || v > b.max + 1e-9) {
    return `${toInput(f, b.min)}~${toInput(f, b.max)}${f.unit} 범위여야 한다`;
  }
  return f.percent ? Math.round(v * 10000) / 10000 : v;
}

interface Props {
  data: SettingsResponse;
  onSave: (s: PlacementSettings) => Promise<void>;
}

export function SettingsTab({ data, onSave }: Props) {
  const fromSettings = (s: PlacementSettings) =>
    Object.fromEntries(FIELDS.map((f) => [f.key, toInput(f, s[f.key])])) as Record<Key, string>;
  const [form, setForm] = useState<Record<Key, string>>(() => fromSettings(data.settings));
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  // 서버 값이 바뀌면(다른 사람 저장·실행 시 재조회·내 저장) 폼을 새 값으로 다시 채운다.
  // 옛 폼 값이 남으면 손대지 않은 필드까지 예전 값으로 되돌려 저장하게 된다.
  // (렌더 중 이전 prop과 비교해 상태를 맞추는 React 권장 패턴 -- effect보다 한 번 덜 그린다.)
  const [seenUpdatedAt, setSeenUpdatedAt] = useState(data.updated_at);
  if (seenUpdatedAt !== data.updated_at) {
    setSeenUpdatedAt(data.updated_at);
    setForm(fromSettings(data.settings));
  }

  const parsed = FIELDS.map((f) => [f.key, parseField(f, form[f.key], data.bounds[f.key])] as const);
  const errors = Object.fromEntries(parsed.filter(([, v]) => typeof v === "string"));
  const valid = Object.keys(errors).length === 0;
  const next = valid ? (Object.fromEntries(parsed) as unknown as PlacementSettings) : null;
  const dirty = next !== null && FIELDS.some((f) => next[f.key] !== data.settings[f.key]);

  async function save() {
    if (!next) return;
    setSaving(true);
    setMessage(null);
    try {
      await onSave(next);
      setMessage("저장했다. 다음 '최적화 실행'부터 이 설정으로 계산한다.");
    } catch (e) {
      setMessage(String(e));
    } finally {
      setSaving(false);
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
                value={form[f.key]}
                onChange={(e) => setForm({ ...form, [f.key]: e.target.value })}
                aria-invalid={f.key in errors}
                aria-describedby={`set-${f.key}-help${f.key in errors ? ` set-${f.key}-err` : ""}`}
                className={`w-28 rounded-md border px-2 py-1 text-sm tabular-nums ${
                  f.key in errors ? "border-red-400" : "border-slate-300"}`}
              />
              <span className="text-sm text-slate-500">{f.unit}</span>
            </div>
            {f.key in errors && (
              <p id={`set-${f.key}-err`} className="mt-1 text-xs text-red-600">
                {errors[f.key] as string}
              </p>
            )}
            <p id={`set-${f.key}-help`} className="mt-1 text-xs text-slate-500">{f.help}</p>
          </div>
        ))}
      </div>
      <div className="mt-4 flex flex-wrap items-center gap-3">
        <button
          onClick={save}
          disabled={!valid || !dirty || saving}
          className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white
                     hover:bg-slate-700 disabled:cursor-not-allowed disabled:bg-slate-300"
        >
          {saving ? "저장 중…" : "저장"}
        </button>
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
