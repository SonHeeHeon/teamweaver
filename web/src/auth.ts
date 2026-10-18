import { API_BASE } from "./api/client";

/** 앱 전체 로그인(2026-10-18): 서버는 로그인하지 않은 `/api/*` 요청을 401로 거절한다.
 *  모든 API 호출을 일일이 고치지 않고, 브라우저 fetch를 한 번 감싸 두 가지만 한다.
 *  1) API 요청에는 세션 쿠키를 싣는다(dev :5173 → :8000은 교차 origin이라 include가 필요하다).
 *  2) 로그인 화면이 쓰는 경로가 아닌데 401이 오면(세션 만료·로그아웃) 'tw:auth-required' 이벤트를 낸다 -- Root가 로그인 화면으로 돌린다.
 *  응답 자체는 바꾸지 않는다(각 호출부의 오류 처리는 그대로). */
export const AUTH_EVENT = "tw:auth-required";

function urlOf(input: RequestInfo | URL): string {
  if (typeof input === "string") return input;
  if (input instanceof URL) return input.href;
  return input.url;
}

export function installAuthFetch(): void {
  const w = window as typeof window & { __twAuthFetch?: boolean };
  if (w.__twAuthFetch) return;
  w.__twAuthFetch = true;
  const original = window.fetch.bind(window);
  window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = urlOf(input);
    const isApi = url.startsWith(`${API_BASE}/api/`) || url.startsWith("/api/");
    const res = await original(input, isApi ? { credentials: "include", ...init } : init);
    if (isApi && res.status === 401 && !/\/api\/admin(\/|$)/.test(url)) {
      window.dispatchEvent(new CustomEvent(AUTH_EVENT));
    }
    return res;
  };
}
