import { useMemo, useState } from "react";
import type { SkillMapRow } from "../api/types";
import {
  BANDS, QUADRANT_LABEL, bandOrder, creditedMostly, distRows, niceStep, placeLabels, radiusOf, summarize, type Box, type Summary,
} from "./skillDistView";

/** 조직 기술 분포(2026-10-22 사용자 "우리 조직 인력들의 기술 분포 … 이런 기술에 대해 경력이 높구나, 이 기술에 강하겠구나").
 *  한눈에 = 넓이(실무 가능 인원) × 깊이(실무 가능자 평균 경력) 사분면, 자세히 = 기술별 경력 띠.
 *  - 칸은 이 조직 기술끼리의 가운데 값으로 나눈 **상대 비교**다(절대 기준 아님 -- 화면에 적는다).
 *  - 경력에는 하위 기술 경력 인정분(70%)이 들어 있다 -- 인정분이 절반 넘는 기술은 점선 원·"(인정)"(표와 같은 규칙, 사용자 결정 10-11).
 *  표의 필터와 상관없이 조직 전체(실무 가능 1명 이상인 기술). 경력 개월 기준이고 실제 성과와는 별개(NOT_CALIBRATED). */

const CAT: Record<string, { ko: string; color: string }> = {
  Data: { ko: "데이터", color: "#1f5f4a" }, Domain: { ko: "업무 도메인", color: "#8a6514" }, AI: { ko: "AI", color: "#3b5ba5" },
  "Programming Language": { ko: "언어", color: "#6b4fa0" }, Cloud: { ko: "클라우드", color: "#0e7490" },
  Automation: { ko: "자동화", color: "#a14a4a" }, Framework: { ko: "프레임워크", color: "#5b6b2f" },
  Middleware: { ko: "미들웨어", color: "#7a4e2d" }, Database: { ko: "데이터베이스", color: "#2d5f7a" }, DevOps: { ko: "DevOps", color: "#0f766e" },
  Method: { ko: "방법론", color: "#6b7280" }, Infra: { ko: "인프라", color: "#475569" }, Security: { ko: "보안", color: "#9f1239" },
  Solution: { ko: "솔루션", color: "#854d0e" }, "UI Platform": { ko: "UI 플랫폼", color: "#7c3aed" }, Mobile: { ko: "모바일", color: "#be185d" },
  Architecture: { ko: "아키텍처", color: "#334155" },
};
const catOf = (c: string | null) => CAT[c ?? ""] ?? { ko: c ?? "기타", color: "#64748b" };
const BAND_COLOR = ["#e2e8f0", "#b9d6c9", "#6fa58f", "#2f6f5a", "#0f3d30"];
const TOP = 16;

const W = 1000, H = 460, L = 56, R = 20, T = 24, B = 46;

function Quadrants({ rows, s }: { rows: SkillMapRow[]; s: Summary }) {
  const rs = distRows(rows);
  const { xm, ym } = s;
  const maxPractical = Math.max(1, ...rs.map((r) => r.practical));
  const xStep = Math.max(1, niceStep(maxPractical, 8));              // 사람 수는 1명 단위(작은 묶음에서 "0.5명" 눈금이 나왔다)
  const maxX = Math.ceil((maxPractical * 1.06) / xStep) * xStep;
  const ys = rs.map((r) => r.avg_months / 12);
  const yLo = Math.min(...ys), yHi = Math.max(...ys);
  const yStep = niceStep(Math.max(0.5, yHi - yLo), 5);
  const y0 = Math.max(0, Math.floor((yLo - yStep / 2) / yStep) * yStep), y1 = Math.ceil((yHi + yStep / 2) / yStep) * yStep;
  const X = (v: number) => L + (v / maxX) * (W - L - R);
  const Y = (v: number) => T + (1 - (v - y0) / (y1 - y0)) * (H - T - B);
  const maxSenior = Math.max(0, ...rs.map((r) => r.senior));
  const rad = (r: { senior: number }) => radiusOf(r.senior, maxSenior);
  const byPriority = [...rs].sort((a, b) => b.senior - a.senior || b.practical - a.practical);
  // 사분면 글자 자리는 이름표가 쓰지 않는다
  const corners: Box[] = [{ x: W - R - 160, y: T, w: 160, h: 22 }, { x: L, y: T, w: 170, h: 22 },
                          { x: W - R - 150, y: H - B - 22, w: 150, h: 20 }, { x: L, y: H - B - 22, w: 90, h: 20 }];
  const labels = placeLabels(byPriority.map((r) => ({ key: r.skill, x: X(r.practical), y: Y(r.avg_months / 12), r: rad(r), text: r.skill })),
                             { w: W - R, h: H - B, x0: L + 2, y0: T, blocked: corners });
  const xTicks = Array.from({ length: Math.round(maxX / xStep) + 1 }, (_, i) => i * xStep);
  const yTicks = Array.from({ length: Math.round((y1 - y0) / yStep) + 1 }, (_, i) => +(y0 + i * yStep).toFixed(2));
  const cats = [...new Set(rs.map((r) => r.category))];
  return (
    <div>
      <svg width="100%" viewBox={`0 0 ${W} ${H}`} role="img" className="max-w-[1000px]"
           aria-label="기술마다 실무 가능 인원(넓이)과 평균 경력(깊이) -- 아래 '기술별 경력 띠'에 같은 숫자가 글로 있다">
        <rect x={X(xm)} y={T} width={Math.max(0, X(maxX) - X(xm))} height={Math.max(0, Y(ym / 12) - T)} fill="#ecfdf3" />
        <line x1={X(xm)} x2={X(xm)} y1={T} y2={H - B} stroke="#cbd5e1" strokeDasharray="4 4" />
        <line x1={L} x2={W - R} y1={Y(ym / 12)} y2={Y(ym / 12)} stroke="#cbd5e1" strokeDasharray="4 4" />
        <text x={W - R - 6} y={T + 16} fontSize={13} fontWeight={700} fill="#166534" textAnchor="end">넓고 깊음 = 상대적 강점</text>
        <text x={L + 8} y={T + 16} fontSize={12} fontWeight={700} fill="#475569">{QUADRANT_LABEL.expert}</text>
        <text x={W - R - 6} y={H - B - 8} fontSize={12} fontWeight={700} fill="#475569" textAnchor="end">{QUADRANT_LABEL.broad}</text>
        <text x={L + 8} y={H - B - 8} fontSize={12} fill="#64748b">{QUADRANT_LABEL.weak}</text>
        {xTicks.map((v) => <text key={`x${v}`} x={X(v)} y={H - B + 16} fontSize={10} fill="#64748b" textAnchor="middle">{v}명</text>)}
        {yTicks.map((v) => <text key={`y${v}`} x={L - 8} y={Y(v) + 3} fontSize={10} fill="#64748b" textAnchor="end">{v}년</text>)}
        <text x={(L + W) / 2} y={H - 8} fontSize={12} fill="#64748b" textAnchor="middle">넓이: 실무 가능 인원(경력 12개월 이상) →</text>
        <text x={16} y={(T + H - B) / 2} fontSize={12} fill="#64748b" textAnchor="middle" transform={`rotate(-90 16 ${(T + H - B) / 2})`}>
          깊이: 실무 가능자 평균 경력 →</text>
        {[...rs].sort((a, b) => rad(b) - rad(a)).map((r) => {
          const c = catOf(r.category), cr = creditedMostly(r);
          return (
            <circle key={r.skill} cx={X(r.practical)} cy={Y(r.avg_months / 12)} r={rad(r)} fill={c.color} fillOpacity={cr ? 0.25 : 0.5}
                    stroke={c.color} strokeWidth={cr ? 1.6 : 1} strokeDasharray={cr ? "4 3" : undefined} data-skill={r.skill} data-credited={cr}>
              <title>{`${r.skill}(${c.ko}) — 실무 가능 ${r.practical}명(그중 하위 기술 인정 ${r.practical_credited}명) · 평균 ${(r.avg_months / 12).toFixed(1)}년`
                + ` · 5년 이상 ${r.senior}명${r.senior_credited ? `(그중 인정 ${r.senior_credited}명)` : ""}`}</title>
            </circle>
          );
        })}
        {byPriority.map((r) => {
          const at = labels.get(r.skill);
          return at ? <text key={`l-${r.skill}`} x={at.x} y={at.y} fontSize={11} fill="#1f2933" textAnchor={at.anchor}
                            stroke="#fff" strokeWidth={3} paintOrder="stroke" strokeLinejoin="round">{r.skill}</text> : null;
        })}
      </svg>
      <p className="flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-slate-600">
        {cats.map((k) => <span key={k ?? "etc"}><span style={{ color: catOf(k).color }}>●</span> {catOf(k).ko}</span>)}
        <span>원 크기 = 5년 이상 경력자{maxSenior > 0 ? `(가장 큰 원 ${maxSenior}명)` : ""} · 점선 원 = 실무 가능의 절반 이상이 하위 기술 인정 경력 · 이름이 없는 원은 마우스를 올리면 나옵니다</span>
      </p>
    </div>
  );
}

function Bands({ rows }: { rows: SkillMapRow[] }) {
  const [all, setAll] = useState(false);
  const ordered = bandOrder(rows);
  const shown = all ? ordered : ordered.slice(0, TOP);
  const max = Math.max(1, ...ordered.map((r) => r.levels.reduce((s, n) => s + n, 0)));      // 전체 보기를 눌러도 눈금이 그대로
  const dropped = rows.filter((r) => r.practical === 0 && (r.holders ?? 0) > 0).length;
  return (
    <div>
      <p className="flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-slate-600">
        {BANDS.map((b, i) => <span key={b}><span className="mr-1 inline-block h-2.5 w-3 rounded-sm align-middle" style={{ background: BAND_COLOR[i] }} />{b}</span>)}
        <span>— 오른쪽(진한 색)이 길수록 오래 해 본 사람이 많음, 5년 이상 경력자가 많은 순</span>
      </p>
      {/* 한 격자(모든 줄이 같은 막대 칸) -- 줄마다 오른쪽 글 길이가 달라 막대 칸 폭이 달라지면 같은 인원이 다른 길이로 보였다(폴백 리뷰 SHOULD) */}
      <ul className="mt-2 grid grid-cols-[11rem_minmax(0,420px)_minmax(0,1fr)] items-center gap-x-3 gap-y-1" aria-label="기술별 경력 띠">
        {shown.map((r) => {
          const c = catOf(r.category);
          return (
            <li key={r.skill} className="contents text-xs">
              <span className="truncate text-xs" title={r.skill}>
                <b className="text-slate-800">{r.skill}</b> <span className="text-slate-500">{c.ko}</span>
              </span>
              <span className="flex h-3.5 w-full overflow-hidden rounded-sm" role="img"
                    aria-label={`${r.skill} 경력 분포: ${BANDS.map((b, i) => `${b} ${r.levels[i]}명`).join(", ")}`}>
                {r.levels.map((n, i) => <span key={i} title={`${BANDS[i]} ${n}명`} style={{ width: `${(n / max) * 100}%`, background: BAND_COLOR[i] }} />)}
              </span>
              <span className="text-xs text-slate-600">
                5년+ <b className="text-pine-800">{r.senior}</b>{r.senior_credited ? <span className="text-amber-700">(인정 {r.senior_credited})</span> : null}
                {" · "}실무 {r.practical}{r.practical_credited ? <span className="text-amber-700">(인정 {r.practical_credited})</span> : null}
                {" · "}평균 {(r.avg_months / 12).toFixed(1)}년
              </span>
            </li>
          );
        })}
      </ul>
      <div className="mt-1 flex flex-wrap items-center gap-3 text-[11px] text-slate-500">
        {ordered.length > TOP && (
          <button type="button" className="text-xs text-indigo-700 underline" onClick={() => setAll((v) => !v)}>
            {all ? `상위 ${TOP}개만` : `전체 ${ordered.length}개 보기`}
          </button>
        )}
        {dropped > 0 && <span>실무 가능(12개월+)이 없는 기술 {dropped}개는 빼고 셌습니다.</span>}
      </div>
    </div>
  );
}

export function SkillDistribution({ rows }: { rows: SkillMapRow[] }) {
  const s = useMemo(() => summarize(rows), [rows]);
  if (s.count === 0) return null;
  const line = (q: keyof typeof s.groups, n = 5) =>
    s.groups[q].length ? `${s.groups[q].slice(0, n).join(" · ")}${s.groups[q].length > n ? ` 외 ${s.groups[q].length - n}개` : ""}` : "없음";
  return (
    <section className="space-y-3 rounded-lg border border-slate-200 bg-white px-4 py-3" aria-label="조직 기술 분포">
      <div>
        <h3 className="text-sm font-semibold text-slate-900">조직 기술 분포 — 상대적으로 강한 기술</h3>
        <p className="text-[12px] text-slate-500">
          기술 하나 = 원 하나. 오른쪽일수록 할 줄 아는 사람이 많고(넓이), 위일수록 그 사람들의 경력이 깁니다(깊이).
          칸은 <b>이 조직 기술 {s.count}개끼리 견준 가운데 값</b>(실무 {Math.round(s.xm)}명 · {(s.ym / 12).toFixed(1)}년)으로 나눈 상대 비교입니다(절대 기준 아님).
          경력에는 하위 기술 경력의 70% 인정분이 들어 있고, 인정분이 절반 넘는 기술은 점선 원·"(인정)"으로 표시합니다. 경력 개월로 본 분포이고 실제 성과와는 별개입니다.
        </p>
      </div>
      {s.count >= 4 && (
        <ul className="space-y-0.5 text-[13px]" aria-label="분포 요약">
          <li><b className="text-emerald-700">상대적 강점(넓고 깊음)</b>: {line("strong")}</li>
          <li><b className="text-slate-700">소수 전문가(좁고 깊음)</b>: {line("expert")}</li>
          <li><b className="text-slate-700">많이 해 봤지만 얕음</b>: {line("broad")}</li>
        </ul>
      )}
      <Quadrants rows={rows} s={s} />
      <details open>
        <summary className="cursor-pointer text-xs font-semibold text-slate-700">기술별 경력 띠 — 몇 년 해 본 사람이 몇 명인지</summary>
        <div className="mt-2"><Bands rows={rows} /></div>
      </details>
    </section>
  );
}
