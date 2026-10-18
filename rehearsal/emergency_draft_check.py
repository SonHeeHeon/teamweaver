"""긴급 투입 검토 자료의 AI 초안 실측(2026-10-19, claude-a -- 설계 .omc/plan/2026-10-19-review-doc.md 2.1의 측정).

긴급 투입은 AI에 **새로 들어온 사람의 사실만** 넘긴다(`core.kg.justify.focus_input`). 사실 문구·문법은 그대로지만 사실이 줄면
검사기가 다르게 반응할 수 있어, 연말형 묶음의 진행 사업마다 긴급 투입 1명(빼 오기 1명 허용)을 확정 → 검토 자료 → AI 초안 →
되돌리기를 반복해 AI 글이 검사를 통과하는 비율을 잰다. 외부 AI를 부른다(사업당 1회, 약 $0.001).

    uv run python -m rehearsal.emergency_draft_check [사업 수=10]    # → rehearsal/results/emergency-draft-check.json
데이터 폴더를 안 주면 임시 폴더(작업 데이터를 바꾸므로).
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "rehearsal" / "results" / "emergency-draft-check.json"


def main() -> None:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    if not os.environ.get("TEAMWEAVER_DATA_DIR", "").strip():    # 빈 값도 "지정 없음"(data_dir()은 빈 값이면 ~/.teamweaver, Codex 리뷰)
        os.environ["TEAMWEAVER_DATA_DIR"] = tempfile.mkdtemp(prefix="tw-emergency-draft-")
    os.environ.setdefault("TEAMWEAVER_LOGIN", "off")
    # 관리자 비밀번호·토큰이 .env에 있으면 LOGIN=off여도 로그인이 켜진다 -- 이 임시 서버에서는 비운다(빈 값은 .env가 다시 채우지 않는다, Codex 리뷰)
    for k in ("TEAMWEAVER_ADMIN_PASSWORD", "TEAMWEAVER_ADMIN_PASSWORD_HASH", "TEAMWEAVER_ADMIN_TOKEN"):
        os.environ[k] = ""
    os.environ.setdefault("TEAMWEAVER_NIGHTLY", "off")
    os.environ.setdefault("TEAMWEAVER_SKIP_WARM", "1")
    os.environ["TEAMWEAVER_DEMO_BUNDLE"] = str(ROOT / "demo" / "org-n100-yearend")
    from fastapi.testclient import TestClient
    from api.main import app
    rows = []
    with TestClient(app) as c:
        ws = c.get("/api/workspace").json()
        projects = [o["project_id"] for o in ws["ongoing"]][:limit]
        for pid in projects:
            ws = c.get("/api/workspace").json()
            r = c.post("/api/emergency/plan", json={"dataset_version": ws["dataset_version"], "project_id": pid, "n": 1,
                                                    "pull_budget": 1, "note": "측정"})
            if r.status_code != 200 or not r.json()["best"].get("accepted"):
                rows.append({"project": pid, "status": "no_plan", "detail": r.text[:200]})
                continue
            plan = r.json()
            r = c.post("/api/workspace/apply", json={"dataset_version": ws["dataset_version"], "entries": plan["best"]["entries"],
                                                     "source": "emergency", "note": f"{pid} 측정", **plan["apply_hint"]})
            if r.status_code != 200:
                rows.append({"project": pid, "status": "apply_rejected", "detail": r.text[:200]})
                continue
            v = r.json()["workspace"]["dataset_version"]
            try:
                c.post(f"/api/review-docs/{v}/{pid}").raise_for_status()
                t0 = time.time()
                # force: 같은 데이터 폴더로 다시 돌리면 같은 판·같은 자료의 저장 초안이 나와 측정이 아니게 된다(Codex 리뷰)
                d = c.post(f"/api/review-docs/{v}/{pid}/draft", params={"force": True})
                took = round(time.time() - t0, 1)
                d.raise_for_status()
                body = d.json()
                rows.append({"project": pid, "status": "ok", "method": body.get("body_ai_method"), "seconds": took,
                             "joined": [j["person_id"] for j in body["moves"]["joined"]],
                             "moved": sum(1 for j in body["moves"]["joined"] if j["origin"] == "moved"),
                             "chars": len(body.get("body_ai") or "")})
            except Exception as exc:                    # noqa: BLE001 -- 실패도 결과에 남기고 다음 사업으로(결과 맞추기 금지)
                rows.append({"project": pid, "status": "failed", "detail": f"{type(exc).__name__}: {exc}"[:300]})
            finally:
                c.post("/api/workspace/undo", json={"dataset_version": v})     # 실패해도 확정은 되돌린다
            print(rows[-1], flush=True)
    ok = [r for r in rows if r["status"] == "ok"]
    summary = {"measured": len(ok), "ai_accepted": sum(1 for r in ok if r["method"] == "graphrag"),
               "template_fallback": sum(1 for r in ok if r["method"] != "graphrag"),
               "with_pull": sum(1 for r in ok if r["moved"]), "rows": rows}
    OUT.write_text(json.dumps(summary, ensure_ascii=False, indent=1), "utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "rows"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
