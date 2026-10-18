import { useMemo, useState } from "react";
import { adminLogin } from "../api/client";

/** 로그인 화면(2026-10-18 앱 전체 로그인). 왼쪽은 "사람 × 월 투입률" 편성표 무늬 -- 이 제품이 다루는 것 그대로다. */
export function LoginScreen({ onLoggedIn, reason }: { onLoggedIn: () => void; reason?: string | null }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await adminLogin(password, username.trim());
      setPassword("");
      onLoggedIn();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid min-h-screen bg-canvas md:grid-cols-2">
      <section className="flex flex-col justify-between bg-ink px-8 py-10 text-[#DCE7E2] md:px-14 md:py-14">
        <div>
          <Brand light />
          <h1 className="mt-8 text-3xl font-bold tracking-tight text-white md:text-[34px]">SI 인력 편성</h1>
          <p className="mt-3 max-w-[34ch] text-[15px] leading-relaxed text-[#B9CEC6]">
            예산·월별 가용률·등급 정원을 함께 맞춰 편성안을 만들고, 사람을 바꿔 보고, 그 이유를 설명합니다.
          </p>
        </div>
        <Weave />
      </section>
      <section className="flex items-center justify-center px-4 py-10">
        <form onSubmit={submit} className="panel w-full max-w-[380px] p-8" aria-labelledby="login-title">
          <h2 id="login-title" className="text-xl font-bold text-slate-900">로그인</h2>
          <p className="mt-1 text-[13px] text-slate-500">관리자 계정으로 들어갑니다.</p>
          {(error || reason) && (
            <p role="alert" className="mt-5 rounded-md bg-red-50 px-3 py-2 text-[13px] text-red-600">{error ?? reason}</p>
          )}
          <label className="mt-5 block text-[13px] font-medium text-slate-700" htmlFor="login-id">아이디</label>
          <input id="login-id" className="field mt-1.5" autoComplete="username" value={username}
                 onChange={(e) => setUsername(e.target.value)} required autoFocus />
          <label className="mt-4 block text-[13px] font-medium text-slate-700" htmlFor="login-pw">비밀번호</label>
          <input id="login-pw" type="password" className="field mt-1.5" autoComplete="current-password" value={password}
                 onChange={(e) => setPassword(e.target.value)} required />
          <button type="submit" className="btn-primary mt-6 w-full py-2.5 text-[15px]" disabled={busy}>
            {busy ? "확인 중…" : "로그인"}
          </button>
        </form>
      </section>
    </div>
  );
}

/** 로고: 인주색 사각 도장 "편성" + 이름. 인주색은 로고와 "검증" 도장에만 쓴다. */
export function Brand({ light = false }: { light?: boolean }) {
  return (
    <div className="flex items-center gap-2.5">
      <span aria-hidden className="grid h-[34px] w-[34px] place-items-center rounded-[3px] border-2 border-seal bg-seal-soft
                                   text-center text-[11px] font-bold leading-[1.05] text-seal">편<br />성</span>
      <span className={`text-base font-semibold tracking-tight ${light ? "text-white" : "text-slate-900"}`}>TeamWeaver</span>
    </div>
  );
}

/** 사람 × 월 투입률 격자(장식). 값은 고정 시드라 매번 같다. */
function Weave() {
  const cells = useMemo(() => {
    const out: string[] = [];
    let s = 7;
    for (let i = 0; i < 18 * 9; i++) {
      s = (s * 9301 + 49297) % 233280;
      const r = s / 233280;
      out.push(r < 0.18 ? "#B9D5CA" : r < 0.34 ? "#79A898" : r < 0.5 ? "#3D7465" : r < 0.62 ? "#4E8574" : "#1E463D");
    }
    return out;
  }, []);
  return (
    <div aria-hidden className="mt-10 grid max-w-[470px] grid-cols-[repeat(18,minmax(0,1fr))] gap-1">
      {cells.map((c, i) => <i key={i} className="block aspect-square rounded-[2px]" style={{ background: c }} />)}
    </div>
  );
}
