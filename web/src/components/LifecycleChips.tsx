/** 사업 이력 꼬리표(2026-10-19) -- 글은 lifecycle.ts의 lifecycleTags가 정한다. */
import type { Lifecycle } from "../api/types";
import { lifecycleTags } from "./lifecycle";

export function LifecycleChips({ lc, name }: { lc: Lifecycle | null | undefined; name: string }) {
  const tags = lifecycleTags(lc, name);
  if (tags.length === 0) return null;
  return (
    <span className="ml-1.5 inline-flex flex-wrap gap-1 align-middle" aria-label={`${name} 사업 이력`}
          title={lc?.parent ? "외주 인원은 참고값이다 -- 배치 계산은 회사 인력만 다룬다" : undefined}>
      {tags.map((t) => (
        <span key={t} className="inline-block rounded-full border border-pine-200 bg-[#F1F7F4] px-2 py-px text-xs font-medium text-slate-800">{t}</span>
      ))}
    </span>
  );
}
