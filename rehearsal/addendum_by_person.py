"""소명 글 덧붙임 사람별 형식 확인(2026-10-11, claude-a) -- E7c의 AI 원문을 그대로 다시 돌린다(AI 호출 없음, 결정적).

사용자 결정(2026-10-11): AI 글이 빠뜨린 불리한 사실을 서버가 덧붙이는 문단(core/kg/justify.adverse_addendum)을 한 줄 나열에서 사람별 줄로 바꾼다
(E7c에서 28명 팀은 한 줄에 16~85개가 이어졌다). 지시문·문법·검사기(verify)는 그대로라 AI가 쓰는 부분과 채택 여부는 바뀌지 않는다 --
그래서 AI를 다시 부르지 않고, E7c 결과(rehearsal/results/justify-scale-e7c.json.gz)에 저장된 AI 원문을 가짜 클라이언트로 서비스 경로
(api/rag/justification.generate_justification)에 그대로 넣는다.

판정 기준(실행 전에 정했다):
- R1 AI 원문이 있는 호출 전부가 E7c와 같은 결과(채택/탈락)다.
- R2 채택 글의 AI 부분(덧붙임 앞)이 E7c와 글자 하나 다르지 않다.
- R3 채택 글 전부가 E7c 무결성 검사(새 형식, rehearsal/justify_scale_e7c.integrity_problems: 칩·문구·귀속·덧붙임 줄)를 실패 0으로 통과한다.
- R4 덧붙인 사실 집합이 E7c와 같다(형식만 바뀐다).
보고(기준 없음): 덧붙임 문자 수·줄 수·가장 긴 줄, 팀 크기별, 가장 긴 사례의 전후.
사람 ID 집합(귀속 검사용)은 입력 사실의 주인·팀원으로 만든다 -- 글에 나올 수 있는 사람 ID는 사실 문구(팀 명단·함께 일한 사람·평가자·비교 후보)뿐이다.

  uv run python -m rehearsal.addendum_by_person   → rehearsal/results/addendum-by-person.{json,html}
"""
from __future__ import annotations

import gzip
import html
import json
import statistics
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from rehearsal import justify_scale as e7b  # noqa: E402
from rehearsal import justify_scale_e7c as e7c  # noqa: E402

OUT_JSON = ROOT / "rehearsal" / "results" / "addendum-by-person.json"
OUT_HTML = ROOT / "rehearsal" / "results" / "addendum-by-person.html"
BINS = ((1, 10, "1~10명"), (11, 20, "11~20명"), (21, 99, "21명 이상"))


class _Replay:
    """저장된 AI 원문을 돌려주는 가짜 클라이언트(사용량 0)."""

    def __init__(self, text: str):
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({"text": text}, ensure_ascii=False)))],
            usage=SimpleNamespace(prompt_tokens=0, completion_tokens=0))))


def _ai_part(rendered: str, addendum: str | None) -> str:
    return rendered[:len(rendered) - len(addendum) - 1] if addendum else rendered


def _q(xs: list, p: float):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(p * len(xs)))] if xs else None


def run() -> dict:
    from api.rag.justification import generate_justification
    res = json.loads(gzip.decompress(e7c.OUT_JSON.read_bytes()))
    people: dict[str, set] = {}
    for key, meta in res["inputs"].items():
        b = key.split("/", 1)[0]
        people.setdefault(b, set()).update(meta["team"], *(f["owners"] for f in meta["facts"]))
    for b, ids in people.items():           # 검사기의 known_ids는 채택 여부에 영향이 없다(팀 밖 ID는 어느 쪽이든 S3)
        e7b._PERSON_CACHE[b] = ids
        e7b._ID_CACHE[b] = ids | {k.split("/", 1)[1] for k in res["inputs"] if k.startswith(b + "/")}
    out = {"source": "rehearsal/results/justify-scale-e7c.json.gz", "source_commit": res["commit"],
           "commit": subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip(),
           "dirty": bool(subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain", "--", "core", "api", "rehearsal", ":!rehearsal/results"],
                                        capture_output=True, text=True).stdout.strip()),
           "replayed": 0, "no_llm_text": 0, "R1_method_changed": [], "R2_ai_part_changed": [], "R3_integrity": {"checked": 0, "failed": 0, "examples": []},
           "R4_fact_set_changed": [], "rows": []}
    for c in res["calls"]:
        if not c.get("llm_text"):
            out["no_llm_text"] += 1
            continue
        call = f"{c['bundle']}/{c['project_id']}#{c['run']}"
        inp = e7c._inp(res, c)
        new = generate_justification(_Replay(c["llm_text"]), res["model"], inp)
        out["replayed"] += 1
        if new["method"] != c["method"]:
            out["R1_method_changed"].append(call)
            continue
        if c["method"] != "graphrag":
            continue
        if _ai_part(new["text"], new["addendum"]) != _ai_part(c["rendered"], c["addendum"]):
            out["R2_ai_part_changed"].append(call)
        row = {**c, "rendered": new["text"], "addendum": new["addendum"], "appended_adverse": new["appended_adverse"],
               "addendum_groups": new["addendum_groups"]}
        bad = e7c.integrity_problems(row, inp, people[c["bundle"]])
        out["R3_integrity"]["checked"] += 1
        if bad:
            out["R3_integrity"]["failed"] += 1
            if len(out["R3_integrity"]["examples"]) < 5:
                out["R3_integrity"]["examples"].append({"call": call, "problems": bad[:5]})
        if set(new["appended_adverse"]) != set(c["appended_adverse"] or []):
            out["R4_fact_set_changed"].append(call)
        if new["addendum"]:
            lines = new["addendum"].split("\n")[1:]
            out["rows"].append({"call": call, "set": c["set"], "team": c["team"], "facts": len(new["appended_adverse"]),
                                "old_chars": len(c["addendum"]), "new_chars": len(new["addendum"]), "lines": len(lines),
                                "old_longest_line": len(c["addendum"]), "new_longest_line": max(len(x) for x in lines),
                                "old": c["addendum"], "new": new["addendum"]})
    out["pass"] = {"R1": not out["R1_method_changed"], "R2": not out["R2_ai_part_changed"],
                   "R3": out["R3_integrity"]["failed"] == 0 and out["R3_integrity"]["checked"] > 0, "R4": not out["R4_fact_set_changed"]}
    out["summary"] = summarize(out["rows"])
    return out


def summarize(rows: list[dict]) -> dict:
    def stat(rs):
        if not rs:
            return {"n": 0}
        return {"n": len(rs), "facts_p50": statistics.median(r["facts"] for r in rs), "facts_max": max(r["facts"] for r in rs),
                "old_chars_p50": statistics.median(r["old_chars"] for r in rs), "old_chars_max": max(r["old_chars"] for r in rs),
                "new_chars_p50": statistics.median(r["new_chars"] for r in rs), "new_chars_max": max(r["new_chars"] for r in rs),
                "lines_p50": statistics.median(r["lines"] for r in rs), "lines_max": max(r["lines"] for r in rs),
                "new_longest_line_p50": statistics.median(r["new_longest_line"] for r in rs),
                "new_longest_line_p90": _q([r["new_longest_line"] for r in rs], 0.9), "new_longest_line_max": max(r["new_longest_line"] for r in rs)}
    return {"all": stat(rows), **{label: stat([r for r in rows if lo <= r["team"] <= hi]) for lo, hi, label in BINS}}


def render(out: dict) -> None:
    e = html.escape
    s = out["summary"]

    def fmt(v):
        return "—" if v is None else f"{v:,.0f}" if isinstance(v, (int, float)) else e(str(v))
    rows = "".join(
        f"<tr><td>{e(k)}</td><td class='num'>{v['n']}</td>"
        + ("".join(f"<td class='num'>{fmt(v[a])} / {fmt(v[b])}</td>" for a, b in (
            ("facts_p50", "facts_max"), ("old_chars_p50", "old_chars_max"), ("new_chars_p50", "new_chars_max"), ("lines_p50", "lines_max"),
            ("new_longest_line_p50", "new_longest_line_max"))) if v["n"] else "<td colspan='5'>—</td>") + "</tr>"
        for k, v in s.items())
    worst = max(out["rows"], key=lambda r: r["facts"]) if out["rows"] else None
    ok = all(out["pass"].values())
    verdict = "".join(f"<li><b>{k}</b> {'통과' if v else '실패'}</li>" for k, v in out["pass"].items())
    iex = "".join(f"<li>{e(x['call'])}: {e(' / '.join(x['problems']))}</li>" for x in out["R3_integrity"]["examples"])
    ex = (f"<h2>가장 긴 사례: {e(worst['call'])} (팀 {worst['team']}명, 사실 {worst['facts']}개)</h2>"
          f"<p>이전(한 줄, {worst['old_chars']:,}자)</p><pre>{e(worst['old'])}</pre>"
          f"<p>이후(사람별 {worst['lines']}줄, {worst['new_chars']:,}자, 가장 긴 줄 {worst['new_longest_line']:,}자)</p><pre>{e(worst['new'])}</pre>") if worst else ""
    page = f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>덧붙임 사람별 형식</title><style>
:root{{--bg:#fbfaf7;--fg:#222;--muted:#666;--acc:#2f5f8a;--line:#ddd;--box:#edf3f9}}
@media (prefers-color-scheme:dark){{:root{{--bg:#1a1c1e;--fg:#e7e6e3;--muted:#a8a8a8;--acc:#8ab8e0;--line:#3a3a3a;--box:#222b33}}}}
body{{background:var(--bg);color:var(--fg);font:15px/1.65 -apple-system,"Apple SD Gothic Neo","Noto Sans KR",sans-serif;margin:0;padding:28px 16px}}
main{{max-width:1000px;margin:auto}}h2{{color:var(--acc);border-bottom:2px solid var(--line);padding-bottom:.2em;margin-top:2em}}
table{{border-collapse:collapse;width:100%;font-size:.88em}}th,td{{border-bottom:1px solid var(--line);padding:5px 7px;text-align:left;vertical-align:top}}th{{color:var(--muted)}}
.num{{text-align:right;font-variant-numeric:tabular-nums}}.w{{overflow-x:auto}}pre{{white-space:pre-wrap;background:var(--box);border-radius:8px;padding:10px 14px;font-size:.85em}}
.sub{{color:var(--muted)}}
</style></head><body><main>
<h1>소명 글 덧붙임: 사람별 형식 확인</h1>
<p class="sub">E7c 결과({e(out['source_commit'])})의 AI 원문 {out['replayed']}편을 AI 호출 없이 서비스 경로로 다시 돌렸다 · 이 확인 커밋 {e(out['commit'])}{' (작업 중 변경 있음)' if out['dirty'] else ''} ·
가상 데이터, NOT_CALIBRATED</p>
<h2>판정: {'모두 통과' if ok else '실패 있음'}</h2>
<ul>{verdict}</ul>
<p>R1 채택/탈락 같음(바뀐 호출 {len(out['R1_method_changed'])}) · R2 AI 부분 같음(바뀐 글 {len(out['R2_ai_part_changed'])}) ·
R3 무결성 {out['R3_integrity']['checked'] - out['R3_integrity']['failed']}/{out['R3_integrity']['checked']} · R4 덧붙인 사실 집합 같음(바뀐 글 {len(out['R4_fact_set_changed'])}) ·
AI 원문이 없는 호출(호출 오류) {out['no_llm_text']}건은 다시 돌릴 수 없어 뺐다.</p>
{f"<ul>{iex}</ul>" if iex else ""}
<h2>덧붙임 길이(덧붙임이 있는 글만, p50 / 최대)</h2>
<div class="w"><table><tr><th>팀 크기</th><th>글 수</th><th>덧붙인 사실</th><th>이전 문자</th><th>이후 문자</th><th>이후 줄 수(사람)</th><th>이후 가장 긴 줄</th></tr>{rows}</table></div>
<p class="sub">이전 형식은 한 줄이라 "가장 긴 줄" = 문단 전체다. 문자 수가 줄어드는 몫은 반복되던 "DP0082의 "가 줄 머리 하나로 합쳐진 것이다.</p>
{ex}
</main></body></html>"""
    OUT_HTML.write_text(page, encoding="utf-8")


def main():
    out = run()
    OUT_JSON.write_text(json.dumps({k: v for k, v in out.items() if k != "rows"} | {"rows": [
        {k: v for k, v in r.items() if k not in ("old", "new")} for r in out["rows"]]}, ensure_ascii=False, indent=1), encoding="utf-8")
    render(out)
    print(json.dumps({"pass": out["pass"], "replayed": out["replayed"], "R3": {k: v for k, v in out["R3_integrity"].items() if k != "examples"},
                      "summary": out["summary"]}, ensure_ascii=False, indent=1))
    if out["R3_integrity"]["examples"]:
        print(json.dumps(out["R3_integrity"]["examples"], ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
