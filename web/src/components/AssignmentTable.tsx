import type { AssignEntry, Person, Project } from "../api/types";

interface Props {
  entries: AssignEntry[];
  people: Person[];
  projects: Project[];
}

export function AssignmentTable({ entries, people, projects }: Props) {
  const pName = new Map(people.map((p) => [p.id, p]));
  const jName = new Map(projects.map((j) => [j.id, j.name]));
  const byProject = new Map<string, AssignEntry[]>();
  for (const e of entries) {
    const list = byProject.get(e.project_id) ?? [];
    list.push(e);
    byProject.set(e.project_id, list);
  }
  return (
    <div className="space-y-4">
      {[...byProject.entries()].map(([jid, list]) => (
        <div key={jid} className="rounded-lg border border-slate-200 bg-white">
          <div className="border-b border-slate-100 px-3 py-2 text-sm font-medium text-slate-900">
            {jName.get(jid) ?? jid}
            <span className="ml-2 text-xs font-normal text-slate-500">{list.length}명</span>
          </div>
          <ul className="divide-y divide-slate-100">
            {list.map((e) => {
              const p = pName.get(e.person_id);
              return (
                <li key={`${e.person_id}-${e.project_id}`}
                    className="flex items-center justify-between px-3 py-2 text-sm">
                  <span className="text-slate-900">
                    {p?.name ?? e.person_id}
                    <span className="ml-2 text-xs text-slate-500">{p?.grade}</span>
                  </span>
                  <span className="tabular-nums text-slate-600">
                    {(e.alloc * 100).toFixed(0)}%
                  </span>
                </li>
              );
            })}
          </ul>
        </div>
      ))}
    </div>
  );
}
