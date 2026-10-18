import { useCallback, useEffect, useState } from "react";
import App from "./App";
import { fetchAdminStatus, type AdminStatus } from "./api/client";
import { AUTH_EVENT } from "./auth";
import { LoginScreen } from "./components/LoginScreen";

/** 앱 전체 로그인(2026-10-18): 서버가 로그인을 요구하는데 로그인하지 않았으면 앱 대신 로그인 화면.
 *  쓰는 중에 세션이 끝나 API가 401이면(auth.ts가 이벤트를 낸다) 다시 로그인 화면으로 돌린다. */
export default function Root() {
  const [status, setStatus] = useState<AdminStatus | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const [reason, setReason] = useState<string | null>(null);

  const refresh = useCallback(() => {
    fetchAdminStatus().then((s) => { setStatus(s); setFailed(null); })
      .catch((e) => setFailed(String(e)));
  }, []);

  useEffect(() => { refresh(); }, [refresh]);
  useEffect(() => {
    const onExpired = () => {
      setReason("로그인 시간이 끝났습니다. 다시 로그인하세요.");
      refresh();
    };
    window.addEventListener(AUTH_EVENT, onExpired);
    return () => window.removeEventListener(AUTH_EVENT, onExpired);
  }, [refresh]);

  if (failed) return <p className="p-8 text-red-600">서버에 연결하지 못했습니다: {failed}</p>;
  if (!status) return <p className="p-8 text-slate-500">불러오는 중…</p>;
  if (status.login_required && !status.logged_in) {
    return <LoginScreen reason={reason} onLoggedIn={() => { setReason(null); refresh(); }} />;
  }
  return <App onLoggedOut={() => { setReason(null); refresh(); }} />;
}
