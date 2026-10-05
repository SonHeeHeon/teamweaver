import { useState } from "react";
import { adminLogin } from "../api/client";

interface Props {
  /** 로그인에 성공했다 -- App이 관리자 상태를 다시 읽는다. */
  onLoggedIn: () => void;
  reason?: string | null;
}

/** 관리자 로그인 화면(K14). 비밀번호 하나로 8시간 세션을 받는다(HttpOnly 쿠키 -- 화면은 값을 못 본다). */
export function AdminLogin({ onLoggedIn, reason }: Props) {
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!password) return;
    setBusy(true);
    setError(null);
    try {
      await adminLogin(password);
      setPassword("");
      onLoggedIn();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="mx-auto max-w-sm rounded-lg border border-slate-200 bg-white p-6">
      <h2 className="text-lg font-semibold text-slate-900">관리자 로그인</h2>
      <p className="mt-1 text-sm text-slate-500">
        {reason ?? "배치 설정과 데이터 전환은 관리자만 바꿀 수 있다."}
      </p>
      <form onSubmit={submit} className="mt-4 space-y-3">
        <label className="block text-sm text-slate-700" htmlFor="admin-password">비밀번호</label>
        <input id="admin-password" type="password" autoComplete="current-password" value={password}
               onChange={(e) => setPassword(e.target.value)} aria-invalid={error !== null}
               aria-describedby={error ? "admin-login-error" : undefined}
               className="block w-full rounded-md border border-slate-300 px-3 py-2 text-sm" />
        {error && <p id="admin-login-error" role="alert" className="text-sm text-red-700">{error}</p>}
        <button type="submit" disabled={busy || !password}
                className="w-full rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white
                           hover:bg-slate-700 disabled:cursor-not-allowed disabled:bg-slate-300">
          {busy ? "확인 중…" : "로그인"}
        </button>
      </form>
    </section>
  );
}
