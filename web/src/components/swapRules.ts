export type DropSlot = "out" | "in";

/** 한 사람이 여러 프로젝트에 배치될 수 있다(실측: Plan A 112 entries / 78명,
 *  그중 25명이 2개 이상). 따라서 '교체 대상'을 person_id만으로 식별하면 안 된다 --
 *  어느 프로젝트의 자리를 빼는지가 사라지고, entries.find(person_id)는 늘 첫
 *  항목을 집어 엉뚱한 프로젝트를 교체한다. 배치 1건은 (사람, 프로젝트) 쌍으로
 *  식별한다. */
export const entryKey = (personId: string, projectId: string) => `${personId}::${projectId}`;

export function parseEntryKey(key: string): { personId: string; projectId: string } | null {
  const [personId, projectId, ...rest] = key.split("::");
  if (!personId || !projectId || rest.length > 0) return null;
  return { personId, projectId };
}

/** 드래그 칩의 id는 목적지 슬롯을 접두사로 갖는다(`out::p000::j00`, `in::p001`).
 *  셀렉트는 후보 목록을 필터링해 잘못된 선택을 애초에 막지만, 드래그는 아무 칩이나
 *  아무 슬롯에 떨굴 수 있다 -- 접두사가 슬롯과 맞지 않으면 거부해서 두 입력 방식이
 *  같은 규칙을 따르게 한다. 반환하는 value는 해당 셀렉트의 value와 같은 표현이다. */
export function resolveDrop(
  activeId: string, slot: DropSlot,
): { slot: DropSlot; value: string } | null {
  const prefix = `${slot}::`;
  if (!activeId.startsWith(prefix)) return null;
  const value = activeId.slice(prefix.length);
  if (!value) return null;
  if (slot === "out" && parseEntryKey(value) === null) return null;
  if (slot === "in" && value.includes("::")) return null;
  return { slot, value };
}
