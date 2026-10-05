import { useState } from "react";
import {
  DndContext, KeyboardSensor, PointerSensor, useDraggable, useDroppable,
  useSensor, useSensors, type DragEndEvent,
} from "@dnd-kit/core";
import type { AssignEntry, Person, Swap } from "../api/types";
import { entryKey, parseEntryKey, resolveDrop, type DropSlot } from "./swapRules";

interface Props {
  people: Person[];
  entries: AssignEntry[];
  onSwap: (swap: Swap) => void;
  busy: boolean;
  /** 선택이 바뀌면 부른다. 이전 선택으로 검토한 결과는 더 이상 화면의 선택과 맞지 않으므로
   *  App이 버린다(검토하지 않은 교체가 적용되는 것을 막는다, K10). */
  onSelectionChange?: () => void;
}

function PersonChip({ id, label }: { id: string; label: string }) {
  const { attributes, listeners, setNodeRef, transform, isDragging } = useDraggable({ id });
  return (
    <button
      ref={setNodeRef} type="button" {...listeners} {...attributes}
      style={transform
        ? { transform: `translate3d(${transform.x}px, ${transform.y}px, 0)` }
        : undefined}
      className={`cursor-grab rounded-full border px-2 py-0.5 text-xs
                  ${isDragging
                    ? "border-slate-900 bg-slate-900 text-white"
                    : "border-slate-300 bg-white text-slate-700 hover:border-slate-500"}`}
    >
      {label}
    </button>
  );
}

function DropSlotBox({ id, title, value }: { id: DropSlot; title: string; value: string }) {
  const { setNodeRef, isOver } = useDroppable({ id });
  return (
    <div
      ref={setNodeRef}
      className={`rounded-md border-2 border-dashed px-3 py-2 text-xs
                  ${isOver ? "border-slate-900 bg-slate-50" : "border-slate-300"}`}
    >
      <span className="text-slate-500">{title}</span>
      <p className="mt-0.5 text-sm text-slate-900">{value || "여기로 끌어다 놓기"}</p>
    </div>
  );
}

export function SwapControl({ people, entries, onSwap, busy, onSelectionChange }: Props) {
  // 교체 대상은 (사람, 프로젝트) 쌍으로 식별한다 -- swapRules.entryKey 주석 참고.
  const [outKey, setOutKeyState] = useState("");
  const [inId, setInIdState] = useState("");
  const setOutKey = (v: string) => { setOutKeyState(v); onSelectionChange?.(); };
  const setInId = (v: string) => { setInIdState(v); onSelectionChange?.(); };
  const byId = new Map(people.map((p) => [p.id, p]));
  const placed = new Set(entries.map((e) => e.person_id));
  const bench = people.filter((p) => !placed.has(p.id));
  const outEntry = entries.find((e) => entryKey(e.person_id, e.project_id) === outKey);
  const sensors = useSensors(useSensor(PointerSensor), useSensor(KeyboardSensor));

  function handleDragEnd(ev: DragEndEvent) {
    const over = ev.over?.id;
    if (over !== "out" && over !== "in") return;
    const hit = resolveDrop(String(ev.active.id), over);
    if (!hit) return;
    (hit.slot === "out" ? setOutKey : setInId)(hit.value);
  }

  const nameOf = (id: string) => byId.get(id)?.name ?? id;
  const outParsed = outKey ? parseEntryKey(outKey) : null;
  const outLabel = outParsed ? `${nameOf(outParsed.personId)}·${outParsed.projectId}` : "";

  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4">
      <h3 className="mb-3 text-sm font-semibold text-slate-900">인력 교체 (What-if)</h3>

      {/* 셀렉트와 드래그&드롭을 함께 둔다 -- 발표에서는 드래그를 보여주고,
          드래그가 어긋나면 셀렉트로 확실하게 진행할 수 있다. 둘은 같은
          outId/inId 상태를 공유하므로 어느 쪽으로 골라도 결과가 같다. */}
      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <label htmlFor="swap-out" className="block text-xs text-slate-500">교체 대상</label>
          <select
            id="swap-out" aria-label="교체 대상" value={outKey}
            onChange={(e) => setOutKey(e.target.value)}
            className="mt-1 w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm"
          >
            <option value="">선택…</option>
            {entries.map((e) => {
              const k = entryKey(e.person_id, e.project_id);
              return (
                <option key={k} value={k}>
                  {nameOf(e.person_id)} ({e.project_id})
                </option>
              );
            })}
          </select>
        </div>
        <div>
          <label htmlFor="swap-in" className="block text-xs text-slate-500">교체 투입</label>
          <select
            id="swap-in" aria-label="교체 투입" value={inId}
            onChange={(e) => setInId(e.target.value)}
            className="mt-1 w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm"
          >
            <option value="">선택…</option>
            {bench.map((p) => (
              <option key={p.id} value={p.id}>{p.name} ({p.grade})</option>
            ))}
          </select>
        </div>
      </div>

      <DndContext sensors={sensors} onDragEnd={handleDragEnd}>
        <div className="mt-4 grid gap-3 sm:grid-cols-2">
          <DropSlotBox id="out" title="교체 대상" value={outLabel} />
          <DropSlotBox id="in" title="교체 투입" value={inId ? nameOf(inId) : ""} />
        </div>
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          <div>
            <p className="mb-1 text-xs text-slate-500">배치된 인력</p>
            <div className="flex max-h-24 flex-wrap gap-1 overflow-y-auto">
              {entries.map((e) => {
                const k = entryKey(e.person_id, e.project_id);
                return (
                  <PersonChip key={k} id={`out::${k}`}
                              label={`${nameOf(e.person_id)}·${e.project_id}`} />
                );
              })}
            </div>
          </div>
          <div>
            <p className="mb-1 text-xs text-slate-500">대기 인력</p>
            <div className="flex max-h-24 flex-wrap gap-1 overflow-y-auto">
              {bench.map((p) => <PersonChip key={p.id} id={`in::${p.id}`} label={p.name} />)}
            </div>
          </div>
        </div>
      </DndContext>

      <button
        disabled={busy || !outEntry || !inId}
        onClick={() => outEntry && onSwap({
          out_person_id: outEntry.person_id, in_person_id: inId,
          project_id: outEntry.project_id,
        })}
        className="mt-4 rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white
                   hover:bg-slate-700 disabled:cursor-not-allowed disabled:bg-slate-300"
      >
        {busy ? "계산 중…" : "브리핑 생성"}
      </button>
    </div>
  );
}
