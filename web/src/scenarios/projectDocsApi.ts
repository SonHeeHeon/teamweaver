/** 사업 요구 기술 · 발주 자료 API(2026-10-23 사용자 "신규 사업 편성화면에 각 사업에 어떤 기술을 요하는지" · "그 자료를 다운 받을 수 있는 버튼").
 *  서버 `api/routes/project_docs.py`. 발주 자료 = 사전규격 · 제안요청서(RFP) · 과업지시서 · 사업 개요 · 기타(사용자 결정 "발주 자료 + 종류").
 *  자료는 계산 입력이 아니라 올려도 데이터 판(계산 버전)이 바뀌지 않는다. */
import { API_BASE } from "../api/client";
import { call, isDatasetChanged, type Stage } from "./workspaceApi";

export const DOC_KINDS = ["사전규격", "제안요청서(RFP)", "과업지시서", "사업 개요", "기타"] as const;
export type DocKind = (typeof DOC_KINDS)[number];
/** 공공 RFP는 한글(HWP) 파일·묶음이 많다 -- 서버 api/project_docs.FORMATS와 같다 */
export const DOC_EXTS = ["pdf", "hwp", "hwpx", "docx", "xlsx", "pptx", "zip"] as const;
export const DOC_ACCEPT = DOC_EXTS.map((e) => `.${e}`).join(",");
export const DOC_MAX_BYTES = 20 * 1024 * 1024;

export interface ProjectDoc {
  id: string; project_id: string; kind: DocKind; filename: string; size: number; uploaded_at: string | null;
  /** 시연용 가상 자료(저장소 demo/project_docs) -- 지울 수 없다 */
  demo: boolean;
}
export interface DocList { dataset_version: string; max_per_project: number; docs: Record<string, ProjectDoc[]> }
/** 등록한 요구 기술 한 줄. canonical = 사전 대표 이름이 등록한 이름과 다를 때(계산은 대표 이름으로 합친다), months = 원문 경력 개월 */
export interface Requirement { skill: string; canonical: string | null; months: number | null; level: number; headcount: number }

const enc = encodeURIComponent;
export const fetchProjectDocs = () => call<DocList>("/api/project-docs");
/** 자료 글자를 AI로 보낼 곳: 회사 밖일 수 있음 · 사내 · 보내지 않음(키 없음·실데이터 외부 차단) -- 읽기 전에 화면이 알린다 */
export type AiDestination = "external" | "onprem" | "none";
export const fetchRequirements = () =>
  call<{ dataset_version: string; requirements: Record<string, Requirement[]>; ai_destination?: AiDestination }>("/api/project-requirements");
export const uploadProjectDoc = (v: string, projectId: string, kind: DocKind, file: File) =>
  call<{ doc: ProjectDoc }>(`/api/project-docs/${enc(projectId)}?${new URLSearchParams({ dataset_version: v, kind, filename: file.name })}`,
                            { method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: file });
export const deleteProjectDoc = (v: string, projectId: string, id: string) =>
  call<{ deleted: string }>(`/api/project-docs/${enc(projectId)}/${enc(id)}?dataset_version=${enc(v)}`, { method: "DELETE" });
export const projectDocUrl = (projectId: string, id: string) => `${API_BASE}/api/project-docs/${enc(projectId)}/${enc(id)}`;

/** 올리기 전에 화면에서 거르는 것(서버도 같은 규칙 + 파일 첫 바이트로 다시 본다) */
export function docProblem(file: File): string | null {
  const ext = file.name.includes(".") ? file.name.split(".").pop()!.toLowerCase() : "";
  if (!(DOC_EXTS as readonly string[]).includes(ext)) return `${file.name}: 받는 형식은 ${DOC_EXTS.map((e) => e.toUpperCase()).join("·")}이다`;
  if (file.size === 0) return `${file.name}: 빈 파일이다`;
  if (file.size > DOC_MAX_BYTES) return `${file.name}: 한 파일은 20MB까지다`;
  return null;
}

/** 단계에 맞는 기본 종류: 발주 예정 = 사전규격, 제안 중 = 제안요청서, 수주 = 과업지시서 */
export function defaultKind(stage: Stage | null | undefined): DocKind {
  return stage === "예정" ? "사전규격" : stage === "수주" ? "과업지시서" : "제안요청서(RFP)";
}

export function fileSize(n: number): string {
  return n >= 1024 * 1024 ? `${(n / 1024 / 1024).toFixed(1)}MB` : `${Math.max(1, Math.round(n / 1024))}KB`;
}

/** 경력 개월 → "2년" · "1년 6개월" · "6개월" */
export function experience(months: number): string {
  const y = Math.floor(months / 12), m = months % 12;
  return y ? `${y}년${m ? ` ${m}개월` : ""}` : `${m}개월`;
}

/** 등록 양식·자료 관리에서 고른 파일과 종류 */
export interface PendingDoc { file: File; kind: DocKind }

/** 자료를 하나씩 올린다(등록 뒤). 실패한 파일은 이유와 함께 돌려준다 -- 등록 자체는 이미 끝났다.
 *  데이터 판이 바뀌었으면(409) 삼키지 않고 던진다 -- 부르는 쪽이 새 판을 다시 읽어야 한다(폴백 리뷰 S2: 같은 옛 판으로 계속 실패했다). */
export async function uploadAll(version: string, projectId: string, docs: PendingDoc[]): Promise<{ ok: number; failed: string[] }> {
  let ok = 0;
  const failed: string[] = [];
  for (const d of docs) {
    try {
      await uploadProjectDoc(version, projectId, d.kind, d.file);
      ok += 1;
    } catch (e) {
      if (isDatasetChanged(e)) throw e;
      failed.push(`${d.file.name}(${(e as Error).message})`);
    }
  }
  return { ok, failed };
}

/** 발주 자료에서 필요 기술 읽기(2026-10-23 사용자 "그 자료를 넣으면 llm … 을 통해서 분석해서 알아서 필요 기술에 대해 정의해서 등록해주고 사용자가 보고 …
 *  수정 및 삭제", 결정 "a·b 나눠서" -- 읽기 함수·측정은 claude-a `api/rag/rfp_read`, 라우트·버튼은 b). 결과는 **제안**이다 -- 넣을지는 사람이 고른다. */
export interface ReadSkill {
  concept_id: string; name: string; months: number | null; headcount: number | null; quote: string; start: number; end: number;
  kind: "required" | "preferred";
}
export interface ReadMention { concept_id: string; name: string; quote: string }
export interface ReadResult {
  skills: ReadSkill[]; mentioned_not_required: ReadMention[]; unmapped_terms: string[]; method: "ai" | "rule"; model: string | null;
  warnings: string[]; fallback_reason: string | null; ai_available: boolean; usage?: { latency_s?: number } | null; chars: number;
  /** 이번에 자료 글자를 보낸 곳(설정이 바뀌었으면 미리 읽은 안내와 다를 수 있다) */
  ai_destination?: AiDestination;
}
/** 글자를 꺼낼 수 있는 형식(서버 api/project_docs.TEXT_EXTS와 같다) -- 옛 한글(HWP 5.x)도 2026-10-23부터 읽는다(배포용·암호 문서는 서버가 이유와 함께 거절).
 *  ZIP 묶음은 못 읽는다(풀어서 올린다) */
export const TEXT_EXTS = ["pdf", "hwp", "hwpx", "docx", "pptx", "xlsx"] as const;
export function readable(file: File): boolean {
  const ext = file.name.includes(".") ? file.name.split(".").pop()!.toLowerCase() : "";
  return (TEXT_EXTS as readonly string[]).includes(ext);
}
/** 판 번호를 보내지 않는다 -- 읽기는 데이터를 바꾸지 않고(등록이 판을 확인한다), 옛 판으로 409만 반복되던 길을 없앤다(폴백 리뷰 S3) */
export const readRequirements = (file: File, signal?: AbortSignal) =>
  call<ReadResult>(`/api/project-requirements/read?${new URLSearchParams({ filename: file.name })}`,
                   { method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: file, signal });

/** AI를 쓰지 않은 까닭(화면 말) */
export function fallbackText(reason: string | null): string {
  if (!reason) return "";
  if (reason === "no_client") return "AI 키가 없어";
  if (reason === "external_blocked") return "실데이터는 회사 밖 AI로 보내지 않아";
  if (reason.startsWith("llm_error")) return "AI 응답을 받지 못해";
  return reason;
}
