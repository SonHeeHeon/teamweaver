import { useEffect, useRef, useState } from "react";

/** "편성 기록 내보내기": 제목을 붙여 JSON 파일로 받는다. 나중에 데이터 화면에서 다시 불러와 그때 화면 그대로 본다. */
export function ExportRecordDialog({ defaultTitle, onExport, onClose, editedCount = 0 }: {
  defaultTitle: string;
  /** 화면에서 적용한 변경(교체·투입률) 수 -- 기록에는 아직 담기지 않는다(원안만) */
  editedCount?: number;
  onExport: (title: string) => Promise<void>;
  onClose: () => void;
}) {
  const [title, setTitle] = useState(defaultTitle);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => { input.current?.select(); }, []);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape" && !busy) onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, onClose]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await onExport(title.trim());
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-[rgba(21,55,47,.32)] px-4" role="dialog"
         aria-modal="true" aria-labelledby="export-title">
      <form onSubmit={submit} className="panel w-full max-w-[480px] p-6">
        <h2 id="export-title" className="text-lg font-bold text-slate-900">편성 기록 내보내기</h2>
        <p className="mt-1 text-[13.5px] text-slate-500">
          지금 데이터(인력·사업 원천), 편성안, 계산 설정을 파일 하나로 받습니다. 나중에 데이터 화면에서 불러오면 그때 화면 그대로 봅니다.
        </p>
        <label htmlFor="export-name" className="mt-4 block text-[13px] font-medium text-slate-700">제목</label>
        <input id="export-name" ref={input} className="field mt-1.5" value={title} maxLength={120} required
               onChange={(e) => setTitle(e.target.value)} />
        <ul className="mt-3 list-disc space-y-0.5 pl-5 text-[12.5px] text-slate-500">
          {editedCount > 0 && (
            <li className="text-amber-700">화면에서 적용한 변경 {editedCount}건은 담기지 않고, 계산한 원안 편성안이 담깁니다.</li>
          )}
          <li>운영 중 K 비교는 열어 둔 편성 기록에서 온 결과만 담깁니다(새로 계산한 비교는 아직 담기지 않습니다).</li>
        </ul>
        {error && <p role="alert" className="mt-3 rounded-md bg-red-50 px-3 py-2 text-[13px] text-red-600">{error}</p>}
        <div className="mt-5 flex justify-end gap-2">
          <button type="button" className="btn-ghost" onClick={onClose} disabled={busy}>취소</button>
          <button type="submit" className="btn-primary" disabled={busy || !title.trim()}>
            {busy ? "만드는 중…" : "내보내기"}
          </button>
        </div>
      </form>
    </div>
  );
}
