/** 인사팀 검토 자료 API(2026-10-19, claude-a -- api/routes/review_docs). 설계 .omc/plan/2026-10-19-review-doc.md */
import { API_BASE, AdminLoginRequiredError } from "../api/client";

export type ConfirmKind = "new" | "emergency";
export type NoteType = "reply" | "objection";
export type AgreementStatus = "agreed" | "requested" | "hr_assigned";

export interface ReviewFact {
  key: string;
  kind: string;
  text: string;
  adverse: boolean;
  owner: string | null;
  /** move = 옮겨 온 인력 카드 · adverse = 확인할 사실 · appendix = 부록 */
  section: "move" | "adverse" | "appendix";
}

export interface ReviewNote {
  key: string;
  type: NoteType;
  text: string;
  author?: string | null;
  at?: string | null;
  /** 근거 사실이 지금 자료에 없다(다시 계산돼 바뀜) -- 지우지 않고 따로 보인다 */
  orphan?: boolean;
}

export interface Judgment {
  text: string;
  agreement: { status: AgreementStatus | null; memo: string };
  author?: string;
  at?: string;
}

export interface MoveSource {
  project_id: string; project_name: string; alloc_before: number; alloc_after: number; left: boolean;
  score_before: number; score_after: number; unfilled_before: number; unfilled_after: number;
  skills: { skill: string; met_before: number; met_after: number; headcount: number }[];
  backfill: { person_id: string; grade: string | null; from_bench: boolean }[];
}

export interface Joined {
  person_id: string; grade: string | null; alloc: number;
  origin: "moved" | "partly_free" | "bench" | "follow_on";
  /** 부서장이 허용한 프로젝트 동시 수행으로 들어온 사람(원래 사업 유지) -- 사유·메모 */
  concurrency?: { reason: string; memo?: string; granted_at?: string; max_projects?: number } | null;
  sources: MoveSource[];
}

export interface ReviewDocView {
  confirm: { version: string; kind: ConfirmKind; at: string | null; note: string; before_version: string | null;
             /** 이력 종류(apply_auto·apply_manual·apply_emergency, 2026-10-22부터) */
             source?: string | null };
  project_id: string;
  project_name: string;
  revision: number;
  created_at: string;
  updated_at?: string;
  facts: ReviewFact[];
  moves: { target: string; joined: Joined[] };
  team: string[];
  names: Record<string, string>;
  disclaimer: string;
  body_ai: string | null;
  body_ai_method: string | null;
  /** AI 초안(요약)의 사실 검사 기록(2026-10-20) -- 예전 자료·규칙 본문이면 없음 */
  body_ai_check?: BodyCheck | null;
  body: string | null;
  notes: ReviewNote[];
  judgments: Record<string, Judgment>;
  situation_memo: string;
  status: "draft" | "exported";
  exported_at: string | null;
  reverted: boolean;
  /** 확정 취소(2026-10-21)로 잠긴 자료 -- reverted도 함께 참이다 */
  cancelled?: boolean;
  /** in_history · reverted(되돌림 기록) · out_of_history(다른 데이터로 바꿨거나 20판 밖 -- 고치고 내보낼 수 있고 새 초안만 안 됨) */
  history?: "in_history" | "reverted" | "out_of_history";
  active: boolean;
  body_edited: boolean;
}

export interface ConfirmRow {
  version: string; kind: ConfirmKind; at: string | null; note: string; active: boolean; reverted: boolean; cancelled?: boolean;
  source?: string | null;
  projects: { project_id: string; name: string; exists: boolean; status: string | null; joined?: number; movers: number; objections: number }[];
}

export interface EditPatch {
  revision: number;
  body?: string | null;
  notes?: { key: string; type: NoteType; text: string }[];
  judgments?: Record<string, { text: string; agreement: { status: AgreementStatus | null; memo: string } }>;
  situation_memo?: string;
}

export class ReviewConflictError extends Error {}

function headers(token: string | null, json = true): Record<string, string> {
  return { ...(json ? { "Content-Type": "application/json" } : {}), ...(token ? { "X-Admin-Token": token } : {}) };
}

async function check(res: Response, what: string): Promise<Response> {
  if (res.status === 401) throw new AdminLoginRequiredError("관리자 로그인이 필요하다.");
  if (res.ok) return res;
  const body = await res.json().catch(() => ({}));
  const d = body?.detail;
  const msg = `${what}(${res.status}): ${typeof d === "string" ? d : Array.isArray(d) ? d.map((x: any) => x.msg).join(", ") : d?.message ?? ""}`;
  if (res.status === 409 && typeof d === "string" && d.includes("먼저 고쳤다")) throw new ReviewConflictError(msg);
  throw new Error(msg);
}

const base = (v: string, p: string) => `${API_BASE}/api/review-docs/${encodeURIComponent(v)}/${encodeURIComponent(p)}`;

export async function fetchConfirms(): Promise<ConfirmRow[]> {
  const res = await check(await fetch(`${API_BASE}/api/review-docs`, { credentials: "include" }), "검토 자료 목록을 불러오지 못했다");
  return ((await res.json()) as { confirms: ConfirmRow[] }).confirms;
}

export async function createReviewDoc(v: string, p: string, token: string | null = null): Promise<ReviewDocView> {
  const res = await fetch(base(v, p), { method: "POST", credentials: "include", headers: headers(token), body: "{}" });
  return (await (await check(res, "검토 자료를 만들지 못했다")).json()) as ReviewDocView;
}

export async function fetchReviewDoc(v: string, p: string): Promise<ReviewDocView> {
  const res = await fetch(base(v, p), { credentials: "include" });
  return (await (await check(res, "검토 자료를 불러오지 못했다")).json()) as ReviewDocView;
}

export async function saveReviewDoc(v: string, p: string, patch: EditPatch, token: string | null = null): Promise<ReviewDocView> {
  const res = await fetch(base(v, p), { method: "PUT", credentials: "include", headers: headers(token), body: JSON.stringify(patch) });
  return (await (await check(res, "저장하지 못했다")).json()) as ReviewDocView;
}

export async function draftReviewDoc(v: string, p: string, force = false, token: string | null = null): Promise<ReviewDocView> {
  const res = await fetch(`${base(v, p)}/draft${force ? "?force=true" : ""}`, { method: "POST", credentials: "include",
                                                                                 headers: headers(token), body: "{}" });
  return (await (await check(res, "AI 초안을 만들지 못했다")).json()) as ReviewDocView;
}

export async function downloadReviewPdf(v: string, p: string, token: string | null = null): Promise<void> {
  const res = await check(await fetch(`${base(v, p)}/pdf`, { method: "POST", credentials: "include", headers: headers(token),
                                                             body: "{}" }), "PDF를 만들지 못했다");
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = `review-${p}.pdf`;
  a.click();
  URL.revokeObjectURL(url);
}

/** 검토 자료 AI 요약의 사실 검사(api/routes/review_docs.draft_summary): AI는 사실 번호만 고르고, 저장 전에 검사기가 다시 본다. */
export interface BodyCheck {
  ok: boolean;
  /** graphrag = AI 요약이 검사를 통과 · rule = AI 없음 또는 검사에 걸려 규칙 요약 */
  method: string | null;
  fallback_reason: string | null;
  cited: { id: string; text: string; adverse: boolean }[];
  facts_total: number;
  rules: string;
}

/** AI 초안 요약이 규칙 요약으로 바뀐 까닭(서버 check.fallback_reason). */
export function fallbackLabel(method: string | null, reason: string | null): string {
  if (method === "graphrag") return "AI 요약";
  const why = !reason || reason === "no_client" ? "AI 없이"
    : reason === "ai_busy" ? "AI가 다른 글을 쓰는 중이라"
    : reason.startsWith("verify") || reason === "render_check" || reason === "recheck_failed" ? "AI 요약이 검사에 걸려"
    : reason.startsWith("llm_") ? "AI 호출이 실패해" : reason;
  return method === "rule" ? `규칙 요약(${why})` : `요약 없음(${why}) -- 규칙 본문`;
}

/** 변경 보고서(2026-10-22, api/routes/changes): 판이 바뀐 한 건 -- 사람 변화·사유 줄·상태·인사팀 검토 자료 상태. */
export interface ChangePerson { person_id: string; name: string | null; grade: string | null; from: string[]; to: string[] }
export interface ChangeItem {
  id: string; at: string | null; kind: string; label: string; note: string; by: string | null; dataset_id: string | null;
  version: string;
  /** undone = 되돌림 · cancelled = 확정 취소 · null = 그대로 */
  status: "undone" | "cancelled" | null;
  /** 이 기능 전 이력에서 메운 기록(이름 없음) */
  backfilled: boolean;
  people: { moved: ChangePerson[]; joined: ChangePerson[]; left: ChangePerson[] };
  counts: { moved: number; joined: number; left: number };
  projects: Record<string, string>;
  reasons: string[];
  review: { project_id: string; name: string; exists: boolean; status: string | null; objections: number }[];
  review_kind: boolean;
}

export async function fetchChanges(start: string | null, end: string | null): Promise<ChangeItem[]> {
  const q = new URLSearchParams();
  if (start) q.set("start", start);
  if (end) q.set("end", end);
  const res = await check(await fetch(`${API_BASE}/api/changes?${q}`, { credentials: "include" }), "변경 기록을 불러오지 못했다");
  return ((await res.json()) as { items: ChangeItem[] }).items;
}

/** 고른 변경(한 건이면 건별 보고서)을 한 PDF로 내려받는다. 검토 자료의 "내보냄" 상태는 바꾸지 않는다. */
export async function downloadChangeReport(ids: string[], start: string | null, end: string | null,
                                           token: string | null = null): Promise<void> {
  const res = await check(await fetch(`${API_BASE}/api/changes/report`, {
    method: "POST", credentials: "include", headers: headers(token), body: JSON.stringify({ ids, start, end }),
  }), "변경 보고서 PDF를 만들지 못했다");
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = `change-report-${new Date().toISOString().slice(0, 10)}-${ids.length}.pdf`;
  a.click();
  URL.revokeObjectURL(url);
}
