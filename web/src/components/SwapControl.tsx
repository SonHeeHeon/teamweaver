import { useRef, useState } from "react";
import {
  DndContext, DragOverlay, KeyboardSensor, PointerSensor, useDraggable, useDroppable,
  useSensor, useSensors, type DragEndEvent, type DragOverEvent, type Modifier,
} from "@dnd-kit/core";
import { ProjectCode } from "../ui";
import type { AssignEntry, Person, Project, Swap } from "../api/types";
import { allMonths, blockedMonths, blockedReason } from "./availability";
import { entryKey, parseEntryKey, resolveDrop, type DropSlot } from "./swapRules";

interface Props {
  people: Person[];
  entries: AssignEntry[];
  onSwap: (swap: Swap) => void;
  busy: boolean;
  /** 선택이 바뀌면 부른다. 이전 선택으로 검토한 결과는 더 이상 화면의 선택과 맞지 않으므로
   *  App이 버린다(검토하지 않은 교체가 적용되는 것을 막는다, K10). */
  onSelectionChange?: () => void;
  /** 사업 기간(투입 불가 판정용)과 계획 시작 달(사유의 달 표시). 없으면 판정하지 않는다(예전 동작). */
  projects?: Project[];
  horizonStart?: string | null;
  /** 고른 사람이 바뀌면(관계 목록용) */
  onPick?: (pick: SwapPick) => void;
}

function PersonChip({ id, label, blocked, title }: { id: string; label: string; blocked?: string | null; title?: string }) {
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({ id, disabled: !!blocked });
  if (blocked) {
    // 투입 불가(휴직 등): 보이되 끌 수 없다. 사유는 아래 빨간 목록과 마우스 설명에
    return (
      <span ref={setNodeRef} aria-disabled="true" title={blocked}
            className="cursor-not-allowed rounded-full border border-red-200 bg-slate-100 px-2 py-0.5 text-xs text-slate-400 line-through">
        {label}
      </span>
    );
  }
  // 끌리는 모습은 DragOverlay가 그린다(목록 상자의 스크롤 밖으로 나가도 보이게) -- 원래 자리는 흐리게만
  return (
    <button ref={setNodeRef} type="button" {...listeners} {...attributes} title={title}
            className={`cursor-grab rounded-full border px-2 py-0.5 text-xs ${isDragging
              ? "border-dashed border-slate-400 text-slate-400"
              : "border-slate-300 bg-white text-slate-700 hover:border-slate-500"}`}>
      {label}
    </button>
  );
}

function DropSlotBox({ id, title, value, hint, bad }: { id: DropSlot; title: string; value: string; hint: string; bad: boolean }) {
  const { setNodeRef, isOver } = useDroppable({ id });
  return (
    <div ref={setNodeRef}
         className={`rounded-md border-2 border-dashed px-3 py-2 text-xs ${isOver
           ? bad ? "border-red-500 bg-red-50" : "border-slate-900 bg-slate-50" : "border-slate-300"}`}>
      <span className="text-slate-500">{title}</span>
      <p className="mt-0.5 text-sm text-slate-900">{value || hint}</p>
    </div>
  );
}

/** 교체 투입 값: 대기 인력은 "p001", 다른 사업에서 데려오면 "p001@J011"(그 사업에서 빠진다, 2026-10-20). */
export function parseIn(v: string): { personId: string; from: string | null } {
  const at = v.indexOf("@");
  return at < 0 ? { personId: v, from: null } : { personId: v.slice(0, at), from: v.slice(at + 1) || null };
}

export interface SwapPick { outPersonId: string | null; projectId: string | null; inPersonId: string | null; from: string | null }

export function SwapControl({ people, entries, onSwap, busy, onSelectionChange, projects, horizonStart, onPick }: Props) {
  // 교체 대상은 (사람, 프로젝트) 쌍으로 식별한다 -- swapRules.entryKey 주석 참고.
  const [outKey, setOutKeyState] = useState("");
  const [inId, setInIdState] = useState("");
  const [exchange, setExchange] = useState(false);
  const [dragId, setDragId] = useState<string | null>(null);
  const [overSlot, setOverSlot] = useState<DropSlot | null>(null);
  const [dropMsg, setDropMsg] = useState<string | null>(null);
  const sectionRef = useRef<HTMLDivElement>(null);
  const byId = new Map(people.map((p) => [p.id, p]));
  const jName = new Map((projects ?? []).map((j) => [j.id, j.name]));
  const placed = new Set(entries.map((e) => e.person_id));
  const bench = people.filter((p) => !placed.has(p.id));
  const outEntry = entries.find((e) => entryKey(e.person_id, e.project_id) === outKey);
  const pick = (o: string, i: string) => {
    const op = o ? parseEntryKey(o) : null;
    const ip = i ? parseIn(i) : null;
    onPick?.({ outPersonId: op?.personId ?? null, projectId: op?.projectId ?? null, inPersonId: ip?.personId ?? null, from: ip?.from ?? null });
  };
  const setOutKey = (v: string) => { setOutKeyState(v); onSelectionChange?.(); pick(v, inId); };
  const setInId = (v: string) => { setInIdState(v); setExchange(false); onSelectionChange?.(); pick(outKey, v); };
  const sensors = useSensors(useSensor(PointerSensor), useSensor(KeyboardSensor));
  // 투입 불가 판정(2026-10-19): 교체 대상을 골랐으면 그 사업 기간, 아니면 계획 기간 전체가 막힌 사람만
  const target = outEntry ? projects?.find((j) => j.id === outEntry.project_id) : undefined;
  const targetMonths = target ? Array.from({ length: target.end_month - target.start_month + 1 }, (_, k) => target.start_month + k) : null;
  const blockedOf = (p: Person): string | null => {
    if (!projects) return null;
    if (targetMonths) return blockedReason(p, targetMonths, horizonStart);
    const all = allMonths(p);
    return all.length > 0 && blockedMonths(p, all).length === all.length ? blockedReason(p, all, horizonStart) : null;
  };
  /** 막히지는 않았지만 계획 기간 중 휴직 달이 있는 사람(교체 대상을 고르기 전 안내) */
  const partlyOf = (p: Person): string | null =>
    !projects || targetMonths || blockedOf(p) ? null : blockedReason(p, allMonths(p), horizonStart);
  const blockedIn = bench.map((p) => [p, blockedOf(p)] as const).filter(([, r]) => r);
  const partlyIn = bench.map((p) => [p, partlyOf(p)] as const).filter(([, r]) => r);
  const inParsed = inId ? parseIn(inId) : null;
  const inBlocked = inParsed && byId.get(inParsed.personId) ? blockedOf(byId.get(inParsed.personId) as Person) : null;
  // 다른 사업에서 데려올 수 있는 사람(2026-10-20): 교체 대상의 사업에 이미 있는 사람·교체 대상 본인은 뺀다
  const movable = entries.filter((e) => e.project_id !== outEntry?.project_id && e.person_id !== outEntry?.person_id
    && !entries.some((x) => x.person_id === e.person_id && x.project_id === outEntry?.project_id));
  const movableBy = new Map<string, AssignEntry[]>();
  for (const e of movable) movableBy.set(e.project_id, [...(movableBy.get(e.project_id) ?? []), e]);
  const placedBy = new Map<string, AssignEntry[]>();
  for (const e of entries) placedBy.set(e.project_id, [...(placedBy.get(e.project_id) ?? []), e]);
  const nameOf = (id: string) => byId.get(id)?.name ?? id;

  /** 끄는 칩을 이 칸에 놓을 수 있나 -- 안 되면 이유(사용자 2026-10-20 "이 노드는 여기에 들어갈수 없습니다 라던지 이런게 떠야지") */
  function refusal(active: string, slot: DropSlot): string | null {
    const hit = resolveDrop(active, slot);
    const who = active.startsWith("out::") ? nameOf(parseEntryKey(active.slice(5))?.personId ?? "")
      : nameOf(parseIn(active.slice(4)).personId);
    if (!hit) {
      return slot === "in"
        ? `${who}님은 '교체 투입' 칸에 넣을 수 없습니다 — 배치된 인력은 '교체 대상'(빠질 사람) 칸에 놓습니다. 다른 사업에서 데려오려면 아래 '다른 사업 인력'에서 끕니다.`
        : `${who}님은 '교체 대상' 칸에 넣을 수 없습니다 — 대기·다른 사업 인력은 '교체 투입'(들어올 사람) 칸에 놓습니다.`;
    }
    if (hit.slot === "in") {
      const p = byId.get(parseIn(hit.value).personId);
      const why = p ? blockedOf(p) : null;
      if (why) return `${who}님은 여기에 넣을 수 없습니다 — ${why}`;
    }
    return null;
  }
  function handleDragOver(ev: DragOverEvent) {
    const over = ev.over?.id;
    setOverSlot(over === "out" || over === "in" ? over : null);
  }
  function handleDragEnd(ev: DragEndEvent) {
    setDragId(null);
    setOverSlot(null);
    const over = ev.over?.id;
    if (over !== "out" && over !== "in") return;
    const why = refusal(String(ev.active.id), over);
    if (why) { setDropMsg(why); return; }
    setDropMsg(null);
    const hit = resolveDrop(String(ev.active.id), over);
    if (!hit) return;
    (hit.slot === "out" ? setOutKey : setInId)(hit.value);
  }
  // 끄는 칩은 이 상자 밖으로 나가지 않는다(사용자 2026-10-20 "섹션 밖에선 못나가게 … 내에선 노드들이 움직일 수 있게")
  const insideSection: Modifier = ({ transform, draggingNodeRect }) => {
    const box = sectionRef.current?.getBoundingClientRect();
    if (!box || !draggingNodeRect) return transform;
    return {
      ...transform,
      x: Math.min(Math.max(transform.x, box.left - draggingNodeRect.left), box.right - draggingNodeRect.right),
      y: Math.min(Math.max(transform.y, box.top - draggingNodeRect.top), box.bottom - draggingNodeRect.bottom),
    };
  };

  const outParsed = outKey ? parseEntryKey(outKey) : null;
  const outLabel = outParsed ? `${nameOf(outParsed.personId)} · ${outParsed.projectId}` : "";
  const inLabel = inParsed ? `${nameOf(inParsed.personId)}${inParsed.from ? ` · ${inParsed.from}에서` : ""}` : "";
  const dragLabel = dragId ? (dragId.startsWith("out::") ? (() => { const k = parseEntryKey(dragId.slice(5)); return k ? `${nameOf(k.personId)}·${k.projectId}` : ""; })()
    : nameOf(parseIn(dragId.slice(4)).personId)) : "";
  const liveRefusal = dragId && overSlot ? refusal(dragId, overSlot) : null;

  return (
    <div ref={sectionRef} className="rounded-lg border border-slate-200 bg-white p-4">
      <h3 className="text-sm font-semibold text-slate-900">인력 교체 (What-if)</h3>
      <p className="mb-3 mt-0.5 text-[12.5px] text-slate-500">
        빠질 사람(교체 대상)과 들어올 사람(교체 투입)을 고르면, 교체 전후 점수·규칙 위반·AI 설명을 보여 줍니다. 실제 명단은 '적용'해야 바뀝니다.
        아래에서 고르거나 이름표를 칸으로 끌어다 놓아도 됩니다.
      </p>
      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <label htmlFor="swap-out" className="block text-xs text-slate-500">교체 대상(빠질 사람)</label>
          <select id="swap-out" aria-label="교체 대상" value={outKey} onChange={(e) => setOutKey(e.target.value)}
                  className="mt-1 w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm">
            <option value="">선택…</option>
            {/* 사업마다 묶는다(교체 투입의 "다른 사업에서 데려오기"와 같은 모양, 사용자 2026-10-21). 사업 이름은 묶음 제목에만 --
                항목의 사업 코드는 남긴다(고른 뒤 닫힌 상자에는 항목 글만 보인다) */}
            {[...placedBy.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([jid, list]) => (
              <optgroup key={jid} label={`${jid}${jName.get(jid) ? ` ${jName.get(jid)}` : ""}`}>
                {list.map((e) => {
                  const k = entryKey(e.person_id, e.project_id);
                  const g = byId.get(e.person_id)?.grade;
                  return <option key={k} value={k}>{nameOf(e.person_id)}{g ? ` (${g})` : ""} · {jid}</option>;
                })}
              </optgroup>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor="swap-in" className="block text-xs text-slate-500">교체 투입(들어올 사람)</label>
          <select id="swap-in" aria-label="교체 투입" value={inId} onChange={(e) => setInId(e.target.value)}
                  className="mt-1 w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm">
            <option value="">선택…</option>
            <optgroup label="대기 인력">
              {bench.map((p) => {
                const why = blockedOf(p);
                return <option key={p.id} value={p.id} disabled={!!why}>{p.name} ({p.grade}){why ? ` · ${why}` : ""}</option>;
              })}
            </optgroup>
            {outEntry && [...movableBy.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([jid, list]) => (
              <optgroup key={jid} label={`다른 사업에서 데려오기 — ${jid}${jName.get(jid) ? ` ${jName.get(jid)}` : ""}`}>
                {list.map((e) => {
                  const p = byId.get(e.person_id);
                  const why = p ? blockedOf(p) : null;
                  return <option key={`${e.person_id}@${jid}`} value={`${e.person_id}@${jid}`} disabled={!!why}>
                    {p?.name ?? e.person_id} ({p?.grade}) · {jid}에서{why ? ` · ${why}` : ""}</option>;
                })}
              </optgroup>
            ))}
          </select>
          {!outEntry && <p className="mt-1 text-[11.5px] text-slate-500">교체 대상을 먼저 고르면 다른 사업 인력도 후보에 나옵니다.</p>}
        </div>
      </div>
      {inParsed?.from && outEntry && (
        <div className="mt-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-[12.5px] text-amber-900">
          {nameOf(inParsed.personId)}님을 <ProjectCode id={inParsed.from} name={jName.get(inParsed.from)} />에서 데려오면 그 사업에 자리가 하나 빕니다.
          <label className="ml-2 inline-flex items-center gap-1">
            <input type="checkbox" checked={exchange} onChange={(e) => { setExchange(e.target.checked); onSelectionChange?.(); }} />
            맞바꾸기({nameOf(outEntry.person_id)}님이 {inParsed.from}로 감)
          </label>
        </div>
      )}

      <DndContext sensors={sensors} onDragStart={(ev) => { setDragId(String(ev.active.id)); setDropMsg(null); }}
                  onDragOver={handleDragOver} onDragEnd={handleDragEnd} onDragCancel={() => { setDragId(null); setOverSlot(null); }}>
        <div className="mt-4 grid gap-3 sm:grid-cols-2">
          <DropSlotBox id="out" title="교체 대상(빠질 사람)" value={outLabel} hint="배치된 인력을 여기로 끌어다 놓기"
                       bad={!!dragId && overSlot === "out" && !!liveRefusal} />
          <DropSlotBox id="in" title="교체 투입(들어올 사람)" value={inLabel} hint="대기·다른 사업 인력을 여기로 끌어다 놓기"
                       bad={!!dragId && overSlot === "in" && !!liveRefusal} />
        </div>
        {(liveRefusal || dropMsg) && (
          <p role="alert" className="mt-2 rounded-md bg-red-50 px-2 py-1 text-xs font-medium text-red-700">{liveRefusal ?? dropMsg}</p>
        )}
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          <div>
            <p className="mb-1 text-xs text-slate-500">배치된 인력 → '교체 대상' 칸으로</p>
            <div className="flex max-h-28 flex-wrap gap-1 overflow-y-auto">
              {entries.map((e) => {
                const k = entryKey(e.person_id, e.project_id);
                return <PersonChip key={k} id={`out::${k}`} label={`${nameOf(e.person_id)}·${e.project_id}`}
                                   title={jName.get(e.project_id) ?? undefined} />;
              })}
            </div>
          </div>
          <div>
            <p className="mb-1 text-xs text-slate-500">대기 인력 → '교체 투입' 칸으로</p>
            <div className="flex max-h-28 flex-wrap gap-1 overflow-y-auto">
              {bench.map((p) => <PersonChip key={p.id} id={`in::${p.id}`} label={p.name} blocked={blockedOf(p)} />)}
              {bench.length === 0 && <span className="text-xs text-slate-400">대기 인력 없음</span>}
            </div>
            {outEntry && movable.length > 0 && (
              <>
                <p className="mb-1 mt-2 text-xs text-slate-500">다른 사업 인력(데려오면 그 사업에 자리가 빔) → '교체 투입' 칸으로</p>
                <div className="flex max-h-28 flex-wrap gap-1 overflow-y-auto">
                  {movable.map((e) => {
                    const p = byId.get(e.person_id);
                    return <PersonChip key={`${e.person_id}@${e.project_id}`} id={`in::${e.person_id}@${e.project_id}`}
                                       label={`${p?.name ?? e.person_id}·${e.project_id}`} blocked={p ? blockedOf(p) : null}
                                       title={jName.get(e.project_id) ?? undefined} />;
                  })}
                </div>
              </>
            )}
            {blockedIn.length > 0 && (
              <ul className="mt-1 space-y-0.5 text-xs text-red-600" aria-label="투입 불가 인력">
                {blockedIn.map(([p, why]) => <li key={p.id}>{p.name}: {why}</li>)}
              </ul>
            )}
            {partlyIn.length > 0 && (
              <ul className="mt-1 space-y-0.5 text-xs text-red-600" aria-label="휴직 달이 있는 인력">
                {partlyIn.map(([p, why]) => <li key={p.id}>{p.name}: {why} — 그 달이 걸린 사업에는 넣을 수 없다</li>)}
              </ul>
            )}
          </div>
        </div>
        <DragOverlay modifiers={[insideSection]} dropAnimation={null}>
          {dragId ? (
            <span className={`rounded-full border px-2 py-0.5 text-xs shadow ${liveRefusal
              ? "border-red-500 bg-red-50 text-red-700" : "border-slate-900 bg-slate-900 text-white"}`}>{dragLabel}</span>
          ) : null}
        </DragOverlay>
      </DndContext>

      {inBlocked && (
        <p role="alert" className="mt-3 text-xs font-medium text-red-600">
          {inParsed ? nameOf(inParsed.personId) : ""}: {inBlocked} — 이 사업({outEntry?.project_id}) 기간과 겹쳐 교체 투입할 수 없다
        </p>
      )}
      <button
        disabled={busy || !outEntry || !inParsed || !!inBlocked}
        onClick={() => outEntry && inParsed && onSwap({
          out_person_id: outEntry.person_id, in_person_id: inParsed.personId, project_id: outEntry.project_id,
          ...(inParsed.from ? { in_from_project_id: inParsed.from, ...(exchange ? { exchange: true } : {}) } : {}),
        })}
        className="mt-4 rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white
                   hover:bg-slate-700 disabled:cursor-not-allowed disabled:bg-slate-300"
      >
        {busy ? "계산 중…" : "교체 효과 보기"}
      </button>
    </div>
  );
}
