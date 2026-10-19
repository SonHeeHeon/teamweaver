import type { AssignEntry, Cowork, Person, Project } from "../api/types";
import { ProjectCode } from "../ui";
import type { SwapPick } from "./SwapControl";

/** 평가 극성(-1~1)을 말로 -- 0.3 경계는 화면 표시용(계산에는 원래 값). */
export function reviewWord(r: number | null | undefined): string | null {
  if (r == null) return null;
  return r >= 0.3 ? "평가 좋음" : r <= -0.3 ? "평가 나쁨" : "평가 보통";
}

/** 교체 검토 오른쪽(2026-10-20 사용자 "작은 목록으로"): 빠질 사람·들어올 사람과 그 사업 팀원 사이의 관계만.
 *  예전 전 직원 협업 관계망(회색 원 100개)은 무엇을 보여 주는지 알기 어려워 뺐다. */
export function TeamRelations({ people, projects, coworks, entries, pick }: {
  people: Person[]; projects: Project[]; coworks: Cowork[]; entries: AssignEntry[]; pick: SwapPick | null;
}) {
  const name = new Map(people.map((p) => [p.id, p.name]));
  const jName = new Map(projects.map((j) => [j.id, j.name]));
  const rel = new Map<string, Cowork>();
  for (const c of coworks) rel.set(`${c.a_id}|${c.b_id}`, c), rel.set(`${c.b_id}|${c.a_id}`, c);
  const jid = pick?.projectId ?? null;
  const team = jid ? entries.filter((e) => e.project_id === jid && e.person_id !== pick?.outPersonId).map((e) => e.person_id) : [];
  const rows = (pid: string | null | undefined) => pid ? team.filter((t) => t !== pid).map((t) => ({ t, c: rel.get(`${pid}|${t}`) })) : [];
  const block = (title: string, pid: string | null | undefined) => {
    if (!pid) return null;
    const list = rows(pid);
    const known = list.filter((r) => r.c);
    return (
      <div>
        <p className="text-[13px] font-medium text-slate-800">{title}: {name.get(pid) ?? pid}</p>
        {list.length === 0 ? <p className="text-[12px] text-slate-500">이 사업에 다른 팀원이 없습니다.</p> : (
          <ul className="mt-1 space-y-0.5 text-[12.5px]">
            {known.map(({ t, c }) => (
              <li key={t} className="flex justify-between gap-2">
                <span>{name.get(t) ?? t}</span>
                <span className="text-slate-600">함께 {c!.co_months}개월{reviewWord(c!.review) ? ` · ${reviewWord(c!.review)}` : ""}</span>
              </li>
            ))}
            {list.length > known.length && (
              <li className="text-slate-500">함께 일한 적 없음: {list.filter((r) => !r.c).map((r) => name.get(r.t) ?? r.t).join(", ")}</li>
            )}
          </ul>
        )}
      </div>
    );
  };
  return (
    <section className="rounded-lg border border-slate-200 bg-white p-4" aria-label="팀원과의 관계">
      <h3 className="text-sm font-semibold text-slate-900">팀원과의 관계</h3>
      <p className="mt-0.5 text-[12.5px] text-slate-500">
        교체할 사람들이 {jid ? <><ProjectCode id={jid} name={jName.get(jid)} /> 팀원들과</> : "그 사업 팀원들과"} 함께 일한 기간과 서로 남긴 동료 평가입니다.
        오래 함께했고 평가가 좋으면 협업 점수가 올라갑니다.
      </p>
      {!pick?.outPersonId ? (
        <p className="mt-3 text-[12.5px] text-slate-500">왼쪽에서 교체 대상을 고르면 보입니다.</p>
      ) : (
        <div className="mt-3 space-y-3">
          {block("빠질 사람", pick.outPersonId)}
          {block(pick.from ? `들어올 사람(${pick.from}에서)` : "들어올 사람", pick.inPersonId)}
        </div>
      )}
    </section>
  );
}
