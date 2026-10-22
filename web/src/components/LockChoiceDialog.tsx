/** 연간 계획 계산 전 고정 선택(2026-10-21 사용자 "안을 짜는걸 요청할 때 고정된 사람이 있으면 팝업을 띄워서 먼저 고정된 사람은 계속
 *  고정할지 아니면 고정 풀고 그 사람들도 다시 배치를 할지 물어보고 그 결정을 따라서", claude-a). 지금 데이터에 고정 배치가 있을 때만 뜬다.
 *  유지 = 모든 안(A~D)이 그 배치를 그대로 둔다(`/api/optimize keep_locked`) · 풀기 = 그 사람들도 다시 배치하고, 그 안을 확정하면 고정이 함께 풀린다. */
import type { Meta } from "../api/types";
import { Dialog } from "../ui";

export function LockChoiceDialog({ meta, onKeep, onRelease, onClose }: {
  meta: Meta;
  onKeep: () => void;
  onRelease: () => void;
  onClose: () => void;
}) {
  const locked = meta.locked_assignments ?? [];
  const person = (id: string) => meta.people.find((p) => p.id === id)?.name ?? id;
  const project = (id: string) => { const n = meta.projects.find((p) => p.id === id)?.name; return n ? `${id} ${n}` : id; };
  return (
    <Dialog wide title={`고정한 배치 ${locked.length}건 — 어떻게 짤까요?`} labelId="lock-choice-title" actions={<>
      <button className="btn-ghost" onClick={onClose}>취소</button>
      <button className="btn" onClick={onRelease}>고정 풀고 다시 배치</button>
      <button className="btn-primary" onClick={onKeep}>고정 유지하고 짜기</button>
    </>}>
      <p>지금 데이터에 "이 사업에서 빼지 않는다"고 고정한 사람이 있습니다. 연간 계획은 모두를 처음부터 다시 짭니다.</p>
      <ul className="mt-2 max-h-40 space-y-0.5 overflow-y-auto text-xs text-slate-700" aria-label="고정한 배치">
        {locked.map((l) => <li key={`${l.person_id}@${l.project_id}`}>{person(l.person_id)} — {project(l.project_id)}</li>)}
      </ul>
      <ul className="mt-2 list-disc space-y-0.5 pl-5 text-xs">
        <li><b>고정 유지하고 짜기</b>: 모든 안이 이 사람들을 지금 사업에 그대로 두고 나머지만 짭니다. 나온 안은 그대로 확정할 수 있습니다.</li>
        <li><b>고정 풀고 다시 배치</b>: 이 사람들도 다른 사업으로 옮길 수 있습니다. 그 안을 확정하면 빠지는 고정은 함께 풀리고 기록에 남습니다.</li>
      </ul>
    </Dialog>
  );
}
