import type { PlacementSettings } from "../api/types";

/** 숫자 칸만(투입률 방식은 SettingsTab이 따로 고른다). */
export type NumKey = Exclude<keyof PlacementSettings,
  "allocation_mode" | "time_limit_auto" | "clique_window_months" | "solver_seeds">;

/** 익숙한 쌍(예전 이름 "반복 협업")을 셀 최근 기간. 2026-10-06 사용자 결정: 최근 3년 중 12개월(근거 rehearsal/results/rule-compare.html). */
export const WINDOW_OPTIONS: { value: number | null; label: string }[] = [
  { value: 36, label: "최근 3년(권장)" }, { value: 60, label: "최근 5년" },
  { value: 120, label: "최근 10년" }, { value: null, label: "전체 이력(예전 방식)" },
];

export function windowLabel(v: number | null | undefined): string {
  if (v === null || v === undefined) return "전체 이력";
  return WINDOW_OPTIONS.find((o) => o.value === v)?.label.replace("(권장)", "") ?? `최근 ${v}개월`;
}
type Key = NumKey;

/** 화면 표시 정보. percent=true면 0~1 값을 % 단위로 입력받는다. 범위는 서버
 *  bounds(SettingsResponse.bounds)가 정하고 여기서는 다시 정의하지 않는다. */
export const FIELDS: { key: Key; label: string; unit: string; percent?: boolean;
                       integer?: boolean; help: string }[] = [
  // 이름은 화면 다른 곳과 같은 말로(2026-10-20 사용자 "설정 화면 용어 정리해줘" -- 후보 표의 "익숙한 쌍"·"협업" 등). 저장 키는 그대로
  { key: "min_alloc", label: "최소 투입률", unit: "%", percent: true,
    help: "투입률을 나누는 방식에서 한 사업에 최소한 써야 하는 근무 비율입니다. 실데이터 답변 기준 30%." },
  { key: "max_concurrent_projects", label: "같은 달 최대 사업 수", unit: "개", integer: true,
    help: "투입률을 나누는 방식에서 한 사람이 같은 달에 맡을 수 있는 사업 수입니다(보통 1개, 최대 3개). 전일 투입에서는 부서장이 사람마다 허용합니다." },
  { key: "clique_threshold_months", label: "익숙한 쌍 기준", unit: "개월", integer: true,
    help: "위 '익숙한 쌍을 볼 기간' 안에서 이 개월 이상 함께 일한 두 사람을 '익숙한 쌍'으로 봅니다." },
  { key: "lam", label: "협업 반영 정도", unit: "",
    help: "함께 일해 봤고 서로 평가가 좋은 두 사람을 같은 사업에 넣는 것을 얼마나 우대할지입니다. 협업 점수는 한 사람의 점수가 아니라 두 사람 사이 점수입니다. 0이면 기술만 봅니다. 설계값이며 실제 성과로 맞춘 값이 아닙니다." },
  { key: "mu", label: "익숙한 쌍 감점", unit: "",
    help: "익숙한 쌍을 또 같은 사업에 넣을 때 한 쌍마다 깎는 점수입니다. 늘 같은 사람끼리만 묶이는 것을 막습니다. 설계값이며 실제 성과로 맞춘 값이 아닙니다." },
  { key: "time_limit", label: "계산 시간(안 하나당)", unit: "초", integer: true,
    help: "편성안 하나를 찾는 데 주는 최대 시간입니다. 안 A~D를 차례로 찾으므로 전체는 이보다 몇 배 걸릴 수 있습니다. 모르면 '자동'을 켜 두세요." },
  { key: "gap", label: "계산 멈춤 기준", unit: "%", percent: true,
    help: "이론상 가장 좋은 안과 이만큼 이내로 가까워지면 '충분히 좋다'고 보고 멈춥니다. 작을수록 정확하지만 오래 걸립니다(안 A는 이 값과 1% 중 작은 값)." },
];


type Field = (typeof FIELDS)[number];

/** 입력칸·안내문·PDF가 같은 정밀도로 보이게 한 곳에서 포맷한다(비율은 % 소수 둘째 자리까지). */
export function toInput(f: Field, v: number): string {
  return f.percent ? String(Math.round(v * 10000) / 100) : String(v);
}

export function formatValue(f: Field, v: number): string {
  return `${toInput(f, v)}${f.unit}`;
}

/** 두 설정에서 값이 다른 필드를 "이름 이전→현재"로 나열한다. */
export const MODE_LABEL: Record<PlacementSettings["allocation_mode"], string> = {
  full: "전일 투입(100%)", fixed: "나눠 투입 · 기간 내내 같은 비율", monthly: "나눠 투입 · 달마다 다른 비율",
};

/** 전일 투입에서는 쓰지 않는 칸(투입률을 나누는 예전 방식 전용). */
export const SPLIT_ONLY: NumKey[] = ["min_alloc", "max_concurrent_projects"];

export function describeChanges(before: PlacementSettings, now: PlacementSettings): string[] {
  const out = FIELDS.filter((f) => before[f.key] !== now[f.key])
    .map((f) => `${f.label} ${formatValue(f, before[f.key])}→${formatValue(f, now[f.key])}`);
  const b = before.allocation_mode ?? "full", n = now.allocation_mode ?? "full";
  if (b !== n) out.push(`투입 방식 ${MODE_LABEL[b]}→${MODE_LABEL[n]}`);
  const wb = before.clique_window_months ?? null, wn = now.clique_window_months ?? null;
  if (wb !== wn) out.push(`익숙한 쌍을 볼 기간 ${windowLabel(wb)}→${windowLabel(wn)}`);
  // 자동·수동 표시만 다르고 실제 계산 시간이 같으면 같은 계산이다(편성 기록은 그때의 실제 시간을 "수동"으로 담는다, 2026-10-18).
  const ab = before.time_limit_auto !== false, an = now.time_limit_auto !== false;
  if (ab !== an && before.time_limit !== now.time_limit) out.push(`계산 시간 ${ab ? "자동" : "수동"}→${an ? "자동" : "수동"}`);
  return out;
}
