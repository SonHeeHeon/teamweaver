import { useState } from "react";
import type { Swap, WhatifResponse } from "../api/types";
import { swapWarnings } from "../api/whatifWarnings";

interface Props {
  result: WhatifResponse | null;
  /** 검토한 바로 그 교체. 버튼에 그대로 적어, 무엇이 적용되는지 보이게 한다. */
  swap: Swap | null;
  nameOf: (personId: string) => string;
  busy: boolean;
  onApply: () => void;
}

/** 검토한 교체를 명단에 적용한다(K10). 새 위반·미충원이 있으면 바로 적용하지 않고,
 *  경고를 보여 준 뒤 "위반을 알고 적용"을 한 번 더 눌러야 한다. 브라우저 confirm
 *  대화상자는 쓰지 않는다(모달이 자동화·접근성을 막는다). */
export function ApplyControl({ result, swap, nameOf, busy, onApply }: Props) {
  const [confirming, setConfirming] = useState(false);
  if (!result || !swap) return null;
  const what = `${nameOf(swap.out_person_id)} → ${nameOf(swap.in_person_id)} (${swap.project_id})`;
  const warnings = swapWarnings(result);
  const risky = warnings.length > 0;

  if (confirming && risky) {
    return (
      <div role="alertdialog" aria-labelledby="apply-warn-title"
           className="rounded-lg border border-red-300 bg-red-50 p-4 text-sm text-red-800">
        <p id="apply-warn-title" className="font-semibold">
          {what} 교체를 적용하면 아래 문제가 남는다
        </p>
        <ul className="mt-2 list-inside list-disc">
          {warnings.map((w) => <li key={w}>{w}</li>)}
        </ul>
        <div className="mt-3 flex gap-2">
          <button onClick={() => { setConfirming(false); onApply(); }} disabled={busy}
                  className="rounded-md bg-red-700 px-3 py-1.5 font-medium text-white
                             hover:bg-red-800 disabled:bg-red-300">
            위반을 알고 적용
          </button>
          <button onClick={() => setConfirming(false)}
                  className="rounded-md border border-red-300 bg-white px-3 py-1.5 font-medium">
            취소
          </button>
        </div>
      </div>
    );
  }
  return (
    <div className="space-y-1">
    <p className="text-xs text-slate-500">검토한 교체: {what}</p>
    <button onClick={() => (risky ? setConfirming(true) : onApply())} disabled={busy}
            className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white
                       hover:bg-slate-700 disabled:cursor-not-allowed disabled:bg-slate-300">
      {busy ? "적용 중…" : risky ? "이 교체 적용(경고 있음)" : "이 교체 적용"}
    </button>
    </div>
  );
}
