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
  confirm: { version: string; kind: ConfirmKind; at: string | null; note: string; before_version: string | null };
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
  body: string | null;
  notes: ReviewNote[];
  judgments: Record<string, Judgment>;
  situation_memo: string;
  status: "draft" | "exported";
  exported_at: string | null;
  reverted: boolean;
  /** in_history · reverted(되돌림 기록) · out_of_history(다른 데이터로 바꿨거나 20판 밖 -- 고치고 내보낼 수 있고 새 초안만 안 됨) */
  history?: "in_history" | "reverted" | "out_of_history";
  active: boolean;
  body_edited: boolean;
}

export interface ConfirmRow {
  version: string; kind: ConfirmKind; at: string | null; note: string; active: boolean; reverted: boolean;
  projects: { project_id: string; name: string; exists: boolean; status: string | null; movers: number; objections: number }[];
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
