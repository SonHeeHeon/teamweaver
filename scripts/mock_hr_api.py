"""가짜 인사 시스템 API 서버(2026-10-19) -- 진짜 인사 시스템이 없어 받기 경로(core/ingest/hr_api)를 시험·점검할 때 쓴다.

CSV 묶음 폴더 하나의 인사 표(core.ingest.hr_api.HR_TABLES, 원하면 사업 표도)를 계약(docs/hr-api-contract.md) 모양으로 내준다:
GET /<끝점>?limit=N&cursor=C, Authorization: Bearer <토큰> → {"items": [...], "next": 커서|null}.
값은 CSV 원문 그대로의 문자열이다(빈 칸은 null) -- 받는 쪽이 같은 CSV 계약 검사를 거친다.

    uv run python scripts/mock_hr_api.py --bundle demo/org-n100 --port 8765 --token dev-token
    TEAMWEAVER_HR_API_URL=http://127.0.0.1:8765 TEAMWEAVER_HR_API_TOKEN=dev-token ./run_poc.sh
"""
from __future__ import annotations

import argparse
import csv
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from core.ingest.hr_api import DEFAULT_ENDPOINTS, SERVICE_ENDPOINTS


def load_tables(root: Path, *, with_projects: bool = False) -> dict[str, list[dict]]:
    """끝점 → 항목 목록. 묶음에 없는 표는 빈 목록."""
    names = dict(DEFAULT_ENDPOINTS) | (dict(SERVICE_ENDPOINTS) if with_projects else {})
    out = {}
    for name, ep in names.items():
        path = root / name
        rows = []
        if path.is_file():
            with path.open(encoding="utf-8-sig", newline="") as f:
                rows = [{k: (v if v != "" else None) for k, v in r.items()} for r in csv.DictReader(f)]
        out[ep] = rows
    return out


def respond(tables: dict[str, list[dict]], token: str | None, path: str, query: str,
            authorization: str | None) -> tuple[int, dict]:
    """(HTTP 상태, JSON 본문). 시험은 이 함수를 httpx.MockTransport로 바로 쓴다."""
    if token and authorization != f"Bearer {token}":
        return 401, {"error": "unauthorized"}
    ep = path.strip("/")
    if ep not in tables:
        return 404, {"error": f"unknown endpoint {ep}"}
    q = parse_qs(query)
    try:
        limit = max(1, min(int(q.get("limit", ["500"])[0]), 5000))
        start = int(q.get("cursor", ["0"])[0])
    except ValueError:
        return 400, {"error": "bad limit or cursor"}
    rows = tables[ep]
    page = rows[start:start + limit]
    nxt = start + limit if start + limit < len(rows) else None
    return 200, {"items": page, "next": None if nxt is None else str(nxt)}


def make_server(root: Path | None, port: int, token: str | None, *, with_projects: bool = False,
                host: str = "127.0.0.1", tables: dict[str, list[dict]] | None = None) -> ThreadingHTTPServer:
    """tables(끝점 → 항목)를 주면 그것을 낸다(시험이 고친 원천) -- 서버가 도는 동안 바꾸면 다음 요청부터 바뀐 것을 낸다."""
    if tables is None:
        tables = load_tables(root, with_projects=with_projects)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 -- http.server 이름
            u = urlsplit(self.path)
            status, body = respond(tables, token, u.path, u.query, self.headers.get("Authorization"))
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):          # 조용히
            pass

    return ThreadingHTTPServer((host, port), Handler)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bundle", type=Path, default=Path("demo/org-n100"), help="CSV 묶음 폴더")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--token", default="dev-token", help="빈 문자열이면 인증 없음")
    ap.add_argument("--with-projects", action="store_true", help="사업 표 끝점도 낸다(PMS 연동 시험)")
    a = ap.parse_args()
    srv = make_server(a.bundle, a.port, a.token or None, with_projects=a.with_projects)
    print(f"가짜 인사 시스템: http://127.0.0.1:{a.port} ({a.bundle})")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
