import type { PlacementSettings } from "../api/types";

type Key = keyof PlacementSettings;

/** 화면 표시 정보. percent=true면 0~1 값을 % 단위로 입력받는다. 범위는 서버
 *  bounds(SettingsResponse.bounds)가 정하고 여기서는 다시 정의하지 않는다. */
export const FIELDS: { key: Key; label: string; unit: string; percent?: boolean;
                       integer?: boolean; help: string }[] = [
  { key: "min_alloc", label: "최소 투입률", unit: "%", percent: true,
    help: "한 사람을 한 프로젝트에 배치할 때 최소한 써야 하는 근무 비율. 실데이터 답변 기준 30%." },
  { key: "max_concurrent_projects", label: "동시 프로젝트 최대", unit: "개", integer: true,
    help: "한 사람이 같은 달에 맡을 수 있는 프로젝트 수. 실데이터 답변 기준 최대 3개(보통 1개)." },
  { key: "clique_threshold_months", label: "반복 협업 기준", unit: "개월", integer: true,
    help: "이 기간 이상 함께 일한 두 사람이 또 같은 프로젝트에 배치되면 감점한다." },
  { key: "lam", label: "협업 시너지 가중", unit: "",
    help: "협업 점수를 배치 점수에 얼마나 반영할지. 설계값이며 실제 성과로 보정되지 않았다." },
  { key: "mu", label: "반복 협업 감점", unit: "",
    help: "반복 협업 쌍 하나당 감점. 설계값이며 실제 성과로 보정되지 않았다." },
  { key: "time_limit", label: "계산 시간 한도", unit: "초", integer: true,
    help: "솔버 한 번에 주는 시간이다. 한 번의 실행은 Plan A와 대안을 차례로 풀어 이보다 여러 배 걸릴 수 있다. "
      + "시간 안에 배치를 하나도 찾지 못하면 그 계산은 실패한다." },
  { key: "gap", label: "허용 최적성 차이", unit: "%", percent: true,
    help: "이론상 최선과 이 비율 이내로 가까워지면 계산을 멈춘다(Plan A는 이 값과 1% 중 작은 값)." },
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
export function describeChanges(before: PlacementSettings, now: PlacementSettings): string[] {
  return FIELDS.filter((f) => before[f.key] !== now[f.key])
    .map((f) => `${f.label} ${formatValue(f, before[f.key])}→${formatValue(f, now[f.key])}`);
}
