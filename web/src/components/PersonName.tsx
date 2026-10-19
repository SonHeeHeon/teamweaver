import { useEffect, useRef, useState } from "react";
import type { Person, Project } from "../api/types";
import { ProjectCode } from "../ui";
import { practicalCredits, skillChips } from "./skillLabel";

/** 이름을 누르면 그 사람의 기술 창(배치 명단과 같은 방식, 2026-10-20 화면 친절하게). 사람 정보가 없으면 이름만. */
export function PersonName({ name, person }: { name: string; person?: Person | null }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (ev: MouseEvent) => { if (!(ev.target instanceof Node) || !ref.current?.contains(ev.target)) setOpen(false); };
    const esc = (ev: KeyboardEvent) => { if (ev.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", close); document.removeEventListener("keydown", esc); };
  }, [open]);
  if (!person) return <>{name}</>;
  const chips = skillChips(person);
  return (
    <span ref={ref} className="relative">
      <button type="button" onClick={() => setOpen(!open)} aria-expanded={open} aria-label={`${name} 기술 보기`}
              className="underline decoration-dotted underline-offset-4 hover:text-ink">{name}</button>
      {open && (
        <span role="dialog" aria-label={`${name} 기술`}
              className="absolute left-0 top-full z-20 mt-1 block w-72 rounded-md border border-slate-200 bg-white p-2 text-left shadow-lg">
          <span className="mb-1 block text-[12px] text-slate-600">{name} · {person.grade}</span>
          <span className="flex flex-wrap gap-1">
            {chips.map((c) => (
              <span key={c.skill} className={`rounded px-1.5 py-0.5 text-[11px] ${c.credit
                ? "border border-dashed border-amber-400 bg-amber-50 text-amber-900" : "bg-slate-100 text-slate-700"}`}>
                {c.skill} 레벨 {c.level}{c.credit ? ` (${c.credit})` : ""}
              </span>
            ))}
            {chips.length === 0 && <span className="text-[11px] text-slate-500">등록된 기술 없음</span>}
          </span>
          {practicalCredits(person).length > 0 && (
            <span className="mt-1 block text-[11px] text-amber-800">점선 = 하위 기술 경력에서 일부 인정한 레벨</span>
          )}
        </span>
      )}
    </span>
  );
}

/** "J008, J011에서 빼 옴"의 사업 코드들 -- 마우스를 올리면 사업 이름. */
export function ProjectCodes({ ids, projects }: { ids: string[]; projects?: Project[] }) {
  const name = new Map((projects ?? []).map((j) => [j.id, j.name]));
  return <>{ids.map((id, i) => <span key={id}>{i > 0 && ", "}<ProjectCode id={id} name={name.get(id)} /></span>)}</>;
}
