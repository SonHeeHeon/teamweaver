/** 발주 자료 · 요구 기술 화면 부품(2026-10-23 사용자 "신규사업 제목 오른쪽에 그 자료를 다운 받을 수 있는 버튼" ·
 *  "각 사업에 어떤 기술을 요하는지 … 부서장이 프로젝트 필요 역량을 보고 본인이 아는 정보를 기반해서", claude-b).
 *  - DocLinks: 받기 버튼(종류 이름) — 신규 사업 제목 옆 · 진행 사업 표 줄
 *  - DocsDialog: 신규 사업 자료 관리(추가·지우기) — 진행 사업은 받기만(사용자 결정)
 *  - DocPicker: 등록 양식의 "발주 자료(선택)" 칸 — 등록한 뒤 올린다
 *  - RequirementLine: 등록한 요구 기술(기술 · 경력 n년 이상 · 레벨 · 인원) + 충족 인원(칸 색 = 충족 비율, 2026-10-23) */
import { useState } from "react";
import { Dialog, Spinner } from "../ui";
import {
  DOC_ACCEPT, DOC_KINDS, defaultKind, deleteProjectDoc, docProblem, experience, fileSize, projectDocUrl, uploadAll,
  type DocKind, type PendingDoc, type ProjectDoc, type Requirement,
} from "./projectDocsApi";
import { isDatasetChanged, type SkillCover, type Stage } from "./workspaceApi";

/** 받기 버튼. 같은 종류가 둘 이상이면 뒤에 번호를 붙인다. */
export function DocLinks({ docs }: { docs: ProjectDoc[] }) {
  if (!docs.length) return null;
  const seen = new Map<string, number>();
  return (
    <span className="inline-flex flex-wrap items-center gap-1.5" data-docs="">
      {docs.map((d) => {
        const n = (seen.get(d.kind) ?? 0) + 1;
        seen.set(d.kind, n);
        const label = n > 1 ? `${d.kind} ${n}` : d.kind;
        return (
          <a key={d.id} href={projectDocUrl(d.project_id, d.id)} download={d.filename}
             className="btn inline-flex items-center gap-1 px-2 py-0.5 text-xs font-normal"
             title={`${d.filename} · ${fileSize(d.size)}${d.demo ? " · 시연용 가상 자료" : ""} — 내려받기`}
             aria-label={`발주 자료 ${label} 내려받기(${d.filename})`}>
            <span aria-hidden="true">⤓</span>{label}
          </a>
        );
      })}
    </span>
  );
}

/** 요구 기술 칸 하나의 충족 정도 -- 칸 배경을 충족 비율만큼 초록으로 칠한다(2026-10-23 사용자 "1명을 요구하는데 그 인력이 들어오면 … 전부 초록색,
 *  2명을 요구하는 프로젝트에 한명만 … 반틈만 칠해진다던지"). 판정은 점수와 같은 레벨 기준(서버 `core.evaluate.skill_coverage`). */
function coverFill(c: SkillCover | undefined): string | undefined {
  if (!c || c.met <= 0) return undefined;
  const pct = Math.round(Math.min(1, c.met / Math.max(1, c.headcount)) * 100);
  const color = pct >= 100 ? "#CDEBD9" : "#DFF1E6";
  return `linear-gradient(90deg, ${color} ${pct}%, #FFFFFF ${pct}%)`;
}

function coverTitle(c: SkillCover): string {
  const pct = Math.round(Math.min(1, c.met / Math.max(1, c.headcount)) * 100);
  return [`충족 ${c.met}/${c.headcount}명(${pct}%) — 레벨 ${c.min_level} 이상`,
          c.met_by.length ? `충족: ${c.met_by.join(", ")}` : "",
          c.below_by.length ? `기술은 있으나 레벨이 모자람: ${c.below_by.join(", ")}` : ""].filter(Boolean).join("\n");
}

/** 등록한 요구 기술. 원문 이름이 사전 대표 이름과 다르면 괄호로 함께(계산은 대표 이름으로 합친다).
 *  coverage를 주면 칸마다 충족 인원(지금 팀 또는 "넣기"로 고른 사람까지 친 팀)과 비율 색을, before를 주면 바뀐 수(0→1)를 보인다. */
export function RequirementLine({ reqs, coverage, before }: { reqs: Requirement[] | undefined; coverage?: SkillCover[] | null;
                                                            before?: SkillCover[] | null }) {
  if (reqs === undefined) return null;
  const key = (x: string) => x.trim().toLowerCase();
  const cov = new Map((coverage ?? []).map((c) => [key(c.skill), c]));
  const old = new Map((before ?? []).map((c) => [key(c.skill), c]));
  return (
    <div className="flex flex-wrap items-baseline gap-1.5 text-sm">
      <span className="text-slate-500" title={"신규 사업을 등록할 때 적은 필요 기술 -- 레벨은 경력 개월을 계산과 같은 규칙(1년·3년·5년·8년 경계)으로 바꾼 값"
                                                + (coverage ? "\n칸 색 = 그 기술을 요구 레벨 이상 가진 팀원 수 ÷ 요구 인원(\"넣기\"로 고른 사람 포함)" : "")}>
        요구 기술 ⓘ</span>
      {reqs.length === 0 ? <span className="text-slate-400">등록한 기술 없음</span> : (
        <ul className="inline-flex flex-wrap gap-1.5" aria-label="요구 기술">
          {reqs.map((r, i) => {
            const c = cov.get(key(r.canonical ?? r.skill)) ?? cov.get(key(r.skill));
            const b = c ? old.get(key(c.skill)) : undefined;
            const full = !!c && c.met >= c.headcount;
            return (
              <li key={`${r.skill}-${i}`} className={`rounded-md border px-2 py-0.5 text-xs text-slate-700 ${full ? "border-pine-300" : "border-slate-200"}`}
                  style={{ background: coverFill(c) ?? "#FFFFFF" }} title={c ? coverTitle(c) : undefined}
                  data-cover={c ? `${c.met}/${c.headcount}` : undefined}>
                <b className="font-semibold text-slate-900">{r.skill}</b>
                {r.canonical && <span className="text-slate-500">({r.canonical})</span>}
                <span className="num"> · {r.months !== null ? `경력 ${experience(r.months)} 이상 · ` : ""}레벨 {r.level}{r.months === null ? " 이상" : ""} · {r.headcount}명</span>
                {c && (
                  <span className={`num ml-1 ${full ? "font-semibold text-pine-800" : "text-slate-600"}`} aria-label={`충족 ${c.met}/${c.headcount}명`}>
                    · 충족 {b && b.met !== c.met ? <><s className="text-slate-400">{b.met}</s>→</> : null}{c.met}/{c.headcount}명{full ? " ✓" : ""}
                  </span>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

/** 등록 양식 칸: 고른 파일과 종류(기본은 단계에 맞는 종류). 형식·크기가 맞지 않는 파일은 담지 않고 이유를 보인다. */
export function DocPicker({ value, onChange, stage }: { value: PendingDoc[]; onChange: (v: PendingDoc[]) => void; stage: Stage }) {
  const [problems, setProblems] = useState<string[]>([]);
  function add(files: FileList | null) {
    const list = [...(files ?? [])];
    const bad = list.map(docProblem).filter((p): p is string => !!p);
    const good = list.filter((f) => !docProblem(f)).map((file) => ({ file, kind: defaultKind(stage) }));
    const room = Math.max(0, 10 - value.length);
    if (good.length > room) bad.push(`한 번에 10개까지 올린다 -- ${good.length - room}개는 담지 않았다`);
    setProblems(bad);
    if (good.length && room) onChange([...value, ...good.slice(0, room)]);
  }
  return (
    <div className="space-y-1.5">
      <label className="block">발주 자료(선택)
        <span className="ml-1 text-xs text-slate-500">사전규격·제안요청서(RFP) 등 — PDF·HWP·HWPX·DOCX·XLSX·PPTX·ZIP, 한 파일 20MB</span>
        <input type="file" multiple accept={DOC_ACCEPT} className="mt-1 block w-full text-[13px]" aria-label="발주 자료 파일"
               onChange={(e) => { add(e.target.files); e.target.value = ""; }} />
      </label>
      {value.length > 0 && (
        <ul className="space-y-1" aria-label="올릴 발주 자료">
          {value.map((d, i) => (
            <li key={`${d.file.name}-${i}`} className="flex flex-wrap items-center gap-2 text-[13px]">
              <select className="field w-auto py-0.5 text-xs" aria-label={`${d.file.name} 종류`} value={d.kind}
                      onChange={(e) => onChange(value.map((x, j) => (j === i ? { ...x, kind: e.target.value as DocKind } : x)))}>
                {DOC_KINDS.map((k) => <option key={k} value={k}>{k}</option>)}
              </select>
              <span className="min-w-0 flex-1 truncate">{d.file.name}</span>
              <span className="num text-xs text-slate-500">{fileSize(d.file.size)}</span>
              <button type="button" className="btn-ghost px-1.5 py-0.5 text-xs" onClick={() => onChange(value.filter((_, j) => j !== i))}>빼기</button>
            </li>
          ))}
        </ul>
      )}
      {problems.length > 0 && <ul role="alert" className="text-[12.5px] text-amber-800">{problems.map((p) => <li key={p}>{p}</li>)}</ul>}
    </div>
  );
}

/** 신규 사업 자료 관리: 목록 · 지우기(시연 자료 제외) · 추가. */
export function DocsDialog({ projectId, projectName, stage, docs, version, onChanged, onStale, onClose }: {
  projectId: string; projectName: string; stage: Stage; docs: ProjectDoc[]; version: string;
  onChanged: () => void; onStale: () => void; onClose: () => void;
}) {
  const [pending, setPending] = useState<PendingDoc[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  async function run(key: string, job: () => Promise<void>) {
    setBusy(key);
    setError(null);
    try {
      await job();
    } catch (e) {
      if (isDatasetChanged(e)) { onStale(); return; }
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }
  return (
    <Dialog title={`발주 자료 — ${projectName}`} wide labelId="docs-title"
            actions={<button className="btn-ghost" onClick={onClose} disabled={!!busy}>닫기</button>}>
      <p>발주처가 낸 사전규격·제안요청서(RFP)·과업지시서나 영업이 정리한 사업 개요를 둡니다. 계산에는 쓰지 않습니다.</p>
      {docs.length === 0 ? <p className="mt-3 text-slate-500">아직 올린 자료가 없습니다.</p> : (
        <ul className="mt-3 divide-y divide-slate-100 rounded-md border border-slate-200" aria-label="올린 발주 자료">
          {docs.map((d) => (
            <li key={d.id} className="flex flex-wrap items-center gap-2 px-3 py-2">
              <span className="rounded-full bg-slate-100 px-2 py-px text-xs">{d.kind}</span>
              <a className="min-w-0 flex-1 truncate text-ink underline" href={projectDocUrl(d.project_id, d.id)} download={d.filename}>{d.filename}</a>
              <span className="num text-xs text-slate-500">{fileSize(d.size)}</span>
              {d.demo ? <span className="text-xs text-slate-400" title="시연용 가상 자료는 지울 수 없습니다">시연 자료</span> : (
                <button className="btn-ghost px-1.5 py-0.5 text-xs text-red-700" disabled={!!busy} aria-label={`${d.filename} 지우기`}
                        onClick={() => void run(`del:${d.id}`, async () => { await deleteProjectDoc(version, projectId, d.id); onChanged(); })}>
                  {busy === `del:${d.id}` ? <Spinner status={false} label="지우는 중…" /> : "지우기"}
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      <div className="mt-4 rounded-md border border-dashed border-slate-300 p-3">
        <DocPicker value={pending} onChange={setPending} stage={stage} />
        <button className="btn-primary mt-2" disabled={!pending.length || !!busy}
                onClick={() => void run("up", async () => {
                  const { ok, failed } = await uploadAll(version, projectId, pending);
                  setPending([]);
                  if (ok) onChanged();
                  if (failed.length) setError(`올리지 못한 자료: ${failed.join(", ")}`);
                })}>
          {busy === "up" ? <Spinner status={false} label="올리는 중…" /> : `${pending.length || ""}${pending.length ? "개 " : ""}올리기`}
        </button>
      </div>
      {error && <p role="alert" className="mt-2 rounded-md bg-red-50 px-3 py-2 text-[13px] text-red-700">{error}</p>}
    </Dialog>
  );
}
