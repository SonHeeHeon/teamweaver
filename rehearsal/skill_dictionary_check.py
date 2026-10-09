"""기술 이름 사전 측정(T3, 2026-10-09) -- 계획 .omc/plan/2026-10-09-skill-dictionary.md "검증(구현 전 고정)".

1) 흔들린 사본 회복: 시연 연초 계획 묶음 100·200·300명의 기술 이름(보유·요구) 절반을 사전 별칭으로 바꾼 사본(seed 고정) →
   사전을 쓰면 S 행렬이 원본과 완전히 같아야 한다(최대 차이 0). 사전 없이는 얼마나 무너지는지 보고한다(기준 없음, 위험 설명용).
   100명은 실제로 풀어 "사전 없이 짠 안 A"를 올바른 S로 다시 재 점수 손실을 보인다.
3) 부분 인정 효과(보고, 판정 기준 없음 -- 계수 0.5는 가정값): 계수 0·0.5·1에서 인정 건수, S가 바뀐 (사람, 사업) 쌍, 200명 안 A의 목적값·구성 변화.
   (100명 묶음은 사업 요구에 상위 기술(Java·Python 등)이 없어 부분 인정으로 S가 바뀌지 않는다 -- 그래서 배치 비교는 S가 바뀌는 200명에서 한다.)

  uv run --group benchmark python -m rehearsal.skill_dictionary_check     → rehearsal/results/skill-dictionary.{json,html}
"""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import html
import json
import random
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT_JSON, OUT_HTML = ROOT / "rehearsal" / "results" / "skill-dictionary.json", ROOT / "rehearsal" / "results" / "skill-dictionary.html"
SIZES, SEED = (100, 200, 300), 20261009


def _bundle_dir(n: int, tmp: Path) -> Path:
    from api.datasets import extract_bundle_zip
    src = ROOT / "demo" / f"org-n{n}"
    if src.exists():
        return src
    return extract_bundle_zip((ROOT / "demo" / f"org-n{n}.zip").read_bytes(), tmp / f"orig{n}")


def _variant(rng: random.Random, c: dict) -> str:
    """실데이터형 변형: 버전 표기("X 8", "X 2.x", "X 19c") 또는 괄호 병기("별칭(대표 이름)")."""
    forms = [f"{c['label']} {rng.randint(1, 20)}", f"{c['label']} {rng.randint(1, 5)}.x", f"{c['label']} v{rng.randint(1, 12)}"]
    if "(" not in c["label"] and c.get("aliases"):
        forms.append(f"{rng.choice(c['aliases'])}({c['label']})")
    return rng.choice(forms)


def perturb(src: Path, dst: Path, share: float = 0.5, seed: int = SEED, mode: str = "alias") -> dict:
    """기술 이름(보유·요구)의 share만큼을 바꾼 사본. mode "alias" = 같은 개념의 사전 별칭(배관 점검 -- 사전에서 뽑았으니 회복은 구성상 보장),
    "variant" = 실데이터형 변형(버전 표기·괄호 병기, 사전에 그대로 없는 이름). manifest의 sha256을 다시 쓴다."""
    from core.ingest.skills import load_dictionary
    sd = load_dictionary()
    rng = random.Random(seed)
    shutil.copytree(src, dst)
    changed = 0
    for f in ("person_skills.csv", "project_skill_requirements.csv"):
        with open(dst / f, encoding="utf-8", newline="") as fh:
            r = csv.DictReader(fh)
            cols, rows = r.fieldnames, list(r)
        for row in rows:
            c = sd.lookup(row["skill_name"])
            if c and rng.random() < share:
                if mode == "alias" and c.get("aliases"):
                    row["skill_name"] = rng.choice(c["aliases"])
                elif mode == "variant":
                    row["skill_name"] = _variant(rng, c)
                else:
                    continue
                changed += 1
        with open(dst / f, "w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=cols)
            w.writeheader()
            w.writerows(rows)
    m = json.loads((dst / "manifest.json").read_text("utf-8"))
    if "files" in m:
        m["files"] = {n: hashlib.sha256((dst / n).read_bytes()).hexdigest() for n in m["files"]}
    (dst / "manifest.json").write_text(json.dumps(m, ensure_ascii=False), "utf-8")
    return {"renamed_rows": changed}


def scored(root: Path, **kw):
    from core.graph.memory_graph import MemoryGraph
    from core.ingest.convert import to_dataset
    from core.ingest.loader import load_bundle
    from core.scoring.engine import ScoringEngine
    b, rep = load_bundle(root)
    ds, parsed = to_dataset(b, rep, **kw)
    g = MemoryGraph.build(ds, parsed)
    eng = ScoringEngine(g)
    return ds, g, eng.skill_matrix({}), eng.synergy_matrix(), rep


def s_compare(ga, Sa, gb, Sb) -> dict:
    """같은 사람·사업 순서로 맞춰 비교(사람·사업 id 기준)."""
    pi = [gb.pid_index[p.id] for p in ga.people]
    pj = [gb.project_index[j.id] for j in ga.projects]
    d = Sb[np.ix_(pi, pj)] - Sa
    return {"max_abs_diff": float(np.abs(d).max()), "changed_pairs": int((np.abs(d) > 1e-12).sum()), "pairs": int(d.size),
            "mean_diff": round(float(d.mean()), 4), "min_diff": round(float(d.min()), 4), "max_diff": round(float(d.max()), 4)}


def solve(g, S, C):
    from api.settings import PlacementSettings
    from core.optimize.milp import solve_milp
    params = PlacementSettings().to_milp_params(n_people=len(g.people)).model_copy(update={"solver": "highs"})
    t0 = time.perf_counter()
    plan = solve_milp(g, S, C, params)
    return plan, params, round(time.perf_counter() - t0, 1)


def score_under(g, S, C, params, entries) -> dict:
    from core.evaluate.plan_eval import evaluate_plan
    o = evaluate_plan(g, S, C, params, list(entries)).objective
    return {k: round(getattr(o, k), 3) for k in ("total", "skill", "synergy", "overfamiliarity", "unfilled")}


def run() -> dict:
    res = {"when": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
           "commit": subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip(),
           "recovery": {}, "credit": {}, "plans_100": {}, "plans_200": {}}
    from core.ingest.skills import load_dictionary
    res["dictionary"] = {"version": load_dictionary().version, "concepts": len(load_dictionary().concepts),
                         "sha256": hashlib.sha256((ROOT / "core" / "ingest" / "skill_dictionary.json").read_bytes()).hexdigest()[:16],
                         "skills_py_sha256": hashlib.sha256((ROOT / "core" / "ingest" / "skills.py").read_bytes()).hexdigest()[:16],
                         "convert_py_sha256": hashlib.sha256((ROOT / "core" / "ingest" / "convert.py").read_bytes()).hexdigest()[:16]}
    with tempfile.TemporaryDirectory(prefix="tw-skilldict-") as td:
        tmp = Path(td)
        for n in SIZES:
            src = _bundle_dir(n, tmp)
            ds0, g0, S0, C0, _ = scored(src)                                          # 원본 + 사전(기본)
            pert = perturb(src, tmp / f"pert{n}")
            ds1, g1, S1, C1, rep1 = scored(tmp / f"pert{n}")                         # 흔들린 사본 + 사전
            ds2, g2, S2, C2, rep2 = scored(tmp / f"pert{n}", skill_dictionary=False)  # 흔들린 사본, 사전 없음
            same_people = {p.id: p.skills for p in ds0.people} == {p.id: p.skills for p in ds1.people}
            pv = perturb(src, tmp / f"var{n}", mode="variant")                        # 실데이터형 변형(버전 표기·괄호 병기)
            ds3, g3, S3, C3, rep3 = scored(tmp / f"var{n}")
            unk3 = [i.message for i in rep3.warnings if "사전에 없는" in i.message]
            res.setdefault("variants", {})[str(n)] = {**pv, **s_compare(g0, S0, g3, S3),
                                                      "same_person_levels": {p.id: p.skills for p in ds0.people} == {p.id: p.skills for p in ds3.people},
                                                      "unknown_warnings": unk3}
            held0 = {s for p in ds0.people for s in p.skills}
            held2 = {s for p in ds2.people for s in p.skills}
            unheld2 = sum(1 for j in ds2.projects for r in j.requirements if r.skill not in held2)
            res["recovery"][str(n)] = {**pert, "with_dictionary": {**s_compare(g0, S0, g1, S1), "same_person_levels": same_people},
                                       "without_dictionary": {**s_compare(g0, S0, g2, S2), "distinct_skill_names": len(held2),
                                                              "requirements_nobody_holds": unheld2},
                                       "distinct_skill_names_original": len(held0)}
            print(n, "recovery", res["recovery"][str(n)], flush=True)
            for k in (0.0, 0.5, 1.0):
                dsk, gk, Sk, Ck, repk = scored(src, partial_credit=k)
                note = next((x for x in repk.notes if "부분 인정" in x), "")
                credited = int(note.split("부분 인정 ")[1].split("건")[0]) if "부분 인정 " in note else 0
                res["credit"].setdefault(str(n), {})[str(k)] = {"credited": credited}
                if k == 0.0:
                    base = (gk, Sk)
                else:
                    res["credit"][str(n)][str(k)].update(s_compare(base[0], base[1], gk, Sk))
                if n == 200:
                    plan, params, secs = solve(gk, Sk, Ck)
                    res["plans_200"][f"credit_{k}"] = {"secs": secs, "entries": len(plan.entries), "objective": score_under(gk, Sk, Ck, params, plan.entries),
                                                       "team": sorted(f"{e.person_id}@{e.project_id}" for e in plan.entries)}
            print(n, "credit", res["credit"][str(n)], flush=True)
            if n == 100:
                plan0, params, secs0 = solve(g0, S0, C0)                              # 사전으로 짠 안 A(비교 기준)
                res["plans_100"]["dictionary"] = {"secs": secs0, "objective": score_under(g0, S0, C0, params, plan0.entries)}
                plan, params, secs = solve(g2, S2, C2)                                # 사전 없이(이름이 갈린 S로) 짠 안 A
                # 그 배치를 올바른 S(원본 + 사전)로 다시 잰다 -- 사람·사업 id는 같다
                res["plans_100"]["broken_names_no_dictionary"] = {
                    "secs": secs, "objective_under_wrong_S": score_under(g2, S2, C2, params, plan.entries),
                    "objective_under_true_S": score_under(g0, S0, C0, params, plan.entries)}
                print("plans", {k: v for k, v in res["plans_100"].items()}, flush=True)
    a, b = set(res["plans_200"]["credit_0.0"]["team"]), set(res["plans_200"]["credit_0.5"]["team"])
    res["plans_200"]["team_change_0_vs_0.5"] = {"only_0": len(a - b), "only_0.5": len(b - a), "common": len(a & b)}
    for k in list(res["plans_200"]):
        res["plans_200"][k].pop("team", None)
    OUT_JSON.write_text(json.dumps(res, ensure_ascii=False, indent=1), "utf-8")
    return res


def render(res: dict) -> None:
    e = html.escape
    rec = "".join(
        f"<tr><td>{n}명</td><td class='num'>{r['renamed_rows']:,}</td><td class='num'>{r['with_dictionary']['max_abs_diff']:g}</td>"
        f"<td>{'같음' if r['with_dictionary']['same_person_levels'] else '다름'}</td>"
        f"<td class='num'>{r['without_dictionary']['changed_pairs']:,} / {r['without_dictionary']['pairs']:,}</td>"
        f"<td class='num'>{r['without_dictionary']['mean_diff']:+.3f} (최저 {r['without_dictionary']['min_diff']:+.2f})</td>"
        f"<td class='num'>{r['distinct_skill_names_original']} → {r['without_dictionary']['distinct_skill_names']}</td>"
        f"<td class='num'>{r['without_dictionary']['requirements_nobody_holds']}</td></tr>" for n, r in res["recovery"].items())
    var_rows = "".join(
        f"<tr><td>{n}명</td><td class='num'>{v['renamed_rows']:,}</td><td class='num'>{v['max_abs_diff']:g}</td>"
        f"<td>{'같음' if v['same_person_levels'] else '다름'}</td><td>{e(' / '.join(v['unknown_warnings'])) or '없음'}</td></tr>"
        for n, v in res.get("variants", {}).items())
    cr = ""
    for n, by in res["credit"].items():
        for k, v in by.items():
            cr += (f"<tr><td>{n}명</td><td>{k}</td><td class='num'>{v['credited']:,}</td>"
                   + (f"<td class='num'>{v['changed_pairs']:,} / {v['pairs']:,}</td><td class='num'>{v['mean_diff']:+.4f} (최대 {v['max_diff']:+.3f})</td>"
                      if "changed_pairs" in v else "<td>—(기준)</td><td>—</td>") + "</tr>")
    p = res["plans_200"]
    pr = "".join(f"<tr><td>계수 {k.split('_')[1]}</td><td class='num'>{v['objective']['total']:.2f}</td><td class='num'>{v['objective']['skill']:.2f}</td>"
                 f"<td class='num'>{v['objective']['synergy']:.2f}</td><td class='num'>{v['objective']['unfilled']:.2f}</td><td class='num'>{v['secs']}</td></tr>"
                 for k, v in p.items() if k.startswith("credit_"))
    br = res["plans_100"]["broken_names_no_dictionary"]
    tc = p["team_change_0_vs_0.5"]
    page = f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>기술 이름 사전 측정</title><style>
:root{{--bg:#fbfaf7;--fg:#222;--muted:#666;--acc:#2f6f5e;--line:#ddd}}
@media (prefers-color-scheme:dark){{:root{{--bg:#1b1d1c;--fg:#e8e6e1;--muted:#aaa;--acc:#7cc4ad;--line:#3a3a3a}}}}
body{{background:var(--bg);color:var(--fg);font:15px/1.65 -apple-system,"Apple SD Gothic Neo","Noto Sans KR",sans-serif;margin:0;padding:28px 16px}}
main{{max-width:1000px;margin:auto}}h2{{color:var(--acc);border-bottom:2px solid var(--line);padding-bottom:.2em;margin-top:2em}}
table{{border-collapse:collapse;width:100%;font-size:.9em}}th,td{{border-bottom:1px solid var(--line);padding:5px 7px;text-align:left}}th{{color:var(--muted)}}
.num{{text-align:right;font-variant-numeric:tabular-nums}}.sub{{color:var(--muted);font-size:.85em}}.w{{overflow-x:auto}}
</style></head><body><main>
<h1>기술 이름 사전 측정</h1>
<p class="sub">TeamWeaver · {e(res['when'])} · 커밋 {e(res['commit'])} · 사전 {e(res['dictionary']['version'])}({res['dictionary']['concepts']}개 개념, sha256 {e(res['dictionary'].get('sha256', '?'))}) · 가상 데이터</p>
<h2>1. 흔들린 사본 회복(기준: 사전을 쓰면 S 최대 차이 0)</h2>
<p class="sub">시연 연초 계획 묶음의 기술 이름(보유·요구) 절반을 같은 개념의 다른 이름으로 바꿨다(seed {SEED}). <b>별칭을 사전에서 뽑았으므로 사전이 있으면 회복되는 것은
구성상 보장된다 — 이 표는 입력 단계에 사전이 빠짐없이 연결됐는지 보는 배관 점검이다.</b> "사전 없음" 칸은 행의 절반을 무작위로 바꾼 스트레스 시나리오의 값이다(기술 이름 수 41은 원천 이름 37개 + 부분 인정으로 생긴 상위 기술).</p>
<div class="w"><table><tr><th>규모</th><th>바꾼 행</th><th>사전 O: S 최대 차이</th><th>사전 O: 사람별 레벨</th><th>사전 X: S가 바뀐 쌍</th><th>사전 X: S 평균 변화</th><th>사전 X: 기술 이름 수</th><th>사전 X: 아무도 없는 요구</th></tr>{rec}</table></div>
<p>100명 안 A를 사전 없이(이름이 갈린 S로) 짜면, 그 배치를 올바른 S로 다시 잰 점수는 {br['objective_under_true_S']['total']:.2f}
(그 S로는 {br['objective_under_wrong_S']['total']:.2f}로 보였다) — 사전으로 짠 안 A {res['plans_100']['dictionary']['objective']['total']:.2f}과 비교.</p>
<h3>실데이터형 변형(버전 표기·괄호 병기) — 사전에 그대로 없는 이름</h3>
<p class="sub">같은 절반의 행을 "Java 8", "Spring Boot 2.x", "Oracle v11", "스프링(Spring)" 같은 형태로 바꿨다. 사전의 버전 표기·괄호 병기 규칙이 맞춰야 한다.</p>
<div class="w"><table><tr><th>규모</th><th>바꾼 행</th><th>S 최대 차이</th><th>사람별 레벨</th><th>"사전에 없는 이름" 경고</th></tr>{var_rows}</table></div>
<h2>2. 부분 인정 효과(보고 — 계수 0.5는 근거 데이터 없는 가정값)</h2>
<div class="w"><table><tr><th>규모</th><th>계수</th><th>인정 건수</th><th>S가 바뀐 쌍(계수 0 대비)</th><th>S 평균 변화</th></tr>{cr}</table></div>
<p class="sub">100명 묶음은 사업 요구에 상위 기술(Java·Python 등)이 없어 인정이 생겨도 S가 그대로다. 배치 비교는 S가 바뀌는 200명에서 했다.</p>
<h3>200명 안 A</h3>
<div class="w"><table><tr><th>계수</th><th>목적 합계</th><th>기술</th><th>협업</th><th>빈자리 감점</th><th>풀이 초</th></tr>{pr}</table></div>
<p>계수 0 → 0.5에서 배치(사람@사업) {tc['common']}건 같음, 0에만 {tc['only_0']}건, 0.5에만 {tc['only_0.5']}건. 각 계수의 목적값은 그 계수의 S로 잰 것이라 서로 직접 비교하는 값이 아니다.</p>
</main></body></html>"""
    OUT_HTML.write_text(page, "utf-8")


if __name__ == "__main__":
    out = json.loads(OUT_JSON.read_text("utf-8")) if "--render-only" in sys.argv else run()
    render(out)
    print(json.dumps({k: out[k] for k in ("recovery",)}, ensure_ascii=False)[:600])
