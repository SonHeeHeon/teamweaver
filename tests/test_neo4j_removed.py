"""Plan 4 Task 1: Neo4j 제거가 완전한지 잠그는 테스트.

decision.py의 REMOVAL_PLAN이 실행됐다는 것을 그레핑이 아니라 임포트 실패로
확인한다 -- 파일이 삭제됐는데 어딘가 남은 import가 있으면 여기서 즉시 깨진다.
"""
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_neo4j_store_module_gone():
    assert not (REPO_ROOT / "core" / "graph" / "neo4j_store.py").exists()


def test_neo4j_rag_module_gone():
    assert not (REPO_ROOT / "core" / "rag" / "neo4j_rag.py").exists()


def test_rehydrate_has_no_neo4j_path():
    from core.graph import rehydrate
    assert not hasattr(rehydrate, "from_neo4j")
    assert hasattr(rehydrate, "from_sqlite")


def test_docker_compose_gone():
    assert not (REPO_ROOT / "docker-compose.yml").exists()


def test_neo4j_dependency_gone_from_pyproject():
    text = (REPO_ROOT / "pyproject.toml").read_text("utf-8")
    assert "neo4j" not in text.lower()


# 판정 근거·리포트·이 태스크 범위 밖 파일 중 "neo4j"를 역사적 산문으로 정당하게
# 계속 언급하는 파일들(블록리스트). 코드 리뷰(fix-round 1, 2026-08-16)가 지적한
# 대로, 이 태스크가 실제로 다시 쓴 두 파일(rehydrate.py, exp5_persistence.py)로
# grep 범위를 좁히면 core/rag/sqlite_rag.py나 core/graph/sqlite_store.py처럼
# "지금은 깨끗하지만 미래에 실수로 neo4j import가 재도입될 수 있는" 파일들이
# 회귀 탐지망 밖에 남는다. 그래서 core/ · experiments/ 전체를 훑되, 정당하게
# "neo4j"를 담아야 하는 파일만 명시적으로 뺀다.
_NEO4J_MENTION_ALLOWED = {
    # 판정 근거·리포트 -- 이 태스크에서 byte-identical 보존이 요구된다
    # (2026-08-16 기준 각각 38회·159회 매치, 전부 역사적 산문).
    "experiments/decision.py",
    "experiments/report.py",
    # REMOVAL_PLAN "keep" 목록 -- 과거 비교 서술이 주석으로 남아 있다.
    # sqlite_store.py는 특히 Task 2(참조 무결성 보완) 소관이라 이번 태스크에서
    # 손대지 않았다.
    "core/graph/memory_graph.py",
    "core/graph/sqlite_store.py",
    "core/rag/queries.py",
    # REMOVAL_PLAN 10항목에도 이 태스크의 Files 목록에도 없다 -- Plan 2 실험
    # (exp1_storage.py)과 범용 계측 유틸(harness.py)·플로팅(plots.py)은 그대로
    # 둔다.
    "experiments/bench/harness.py",
    "experiments/bench/exp1_storage.py",
    "experiments/bench/plots.py",
    # exp4_rag.py는 파일 전체를 빼는 대신 아래
    # test_exp4_rag_neo4j_mentions_are_confined_to_documented_prose가 내용
    # 기준으로 더 좁게 검증하므로 여기서는 뺀다.
    "experiments/bench/exp4_rag.py",
}


def test_no_source_file_imports_neo4j_driver():
    """core/ · experiments/ 전체(.py)에서 'neo4j' 문자열을 찾되, 정당하게 그
    단어를 담고 있는 파일만 명시적으로 제외한다(블록리스트). 제외되지 않은
    파일에 'neo4j'가 나타나면 실패한다 -- 이 태스크가 오늘 손대지 않은 파일
    (예: core/rag/sqlite_rag.py)에 미래에 neo4j import가 재도입돼도 잡힌다.

    주의(브리핑 원문과의 의도적 차이): 브리핑 Step 1 원문은 grep 대상을
    "core experiments/bench experiments/decision.py experiments/report.py"로
    한정했지만, 그 조합을 그대로 돌리면 구조적으로 항상 실패한다 --
    decision.py·report.py는 판정 근거를 설명하는 산문에서 "Neo4j"를 역사적
    사실로 계속 언급해야 하고(이 태스크에서 완전 불변이 요구된다), exp4_rag.py는
    브리핑 Step 6의 "최종 형태" 코드 자체가 PRIMING_CALLS 보존 이유를 설명하는
    docstring·주석에 "Neo4j"를 의도적으로 남긴다. 그래서 대상 목록에서 그 파일들을
    완전히 빼는 대신(1차 구현에서 그렇게 했다가 코드 리뷰에서 회귀 탐지력이
    과도하게 좁아진다는 지적을 받았다), 스캔 범위는 core/·experiments/ 전체로
    넓히고 정당한 예외만 블록리스트로 뺐다. (판단 근거 상세는
    .omc/reports/2026-08-16-plan4-task1-neo4j-removal.md 참고.)
    """
    result = subprocess.run(
        ["grep", "-rliI", "--include=*.py", "neo4j", "core", "experiments"],
        cwd=REPO_ROOT, capture_output=True, text=True)
    matched = {line for line in result.stdout.splitlines() if line}
    leftover = matched - _NEO4J_MENTION_ALLOWED
    assert not leftover, (
        f"neo4j 문자열이 남아있는(제외 목록 밖) 파일: {sorted(leftover)}")


def test_exp4_rag_neo4j_mentions_are_confined_to_documented_prose():
    """exp4_rag.py는 위 블록리스트에서 통째로 빠져 있으므로, 파일 전체를 면제해
    주는 대신 여기서 내용 기준으로 더 좁게 검증한다: 남아 있는 'neo4j' 언급은
    전부 PRIMING_CALLS 보존 이유를 설명하는 docstring·주석이어야 하고, import
    문이나 neo4j_store/neo4j_rag/neo4j= 같은 실제 코드 신호가 섞여 있으면
    안 된다 -- 섞여 있으면 neo4j 분기가 재도입됐다는 뜻이다."""
    path = "experiments/bench/exp4_rag.py"
    result = subprocess.run(["grep", "-ni", "neo4j", path],
                            cwd=REPO_ROOT, capture_output=True, text=True)
    lines = [line for line in result.stdout.splitlines() if line]
    assert lines, "PRIMING_CALLS 보존 사유를 설명하는 산문이 사라졌다"
    forbidden = ("import", "neo4j_rag", "neo4j_store", "neo4j=", "neo4j(", "neo4j.")
    for line in lines:
        low = line.lower()
        assert not any(tok in low for tok in forbidden), (
            f"exp4_rag.py에 코드로 보이는 neo4j 언급이 있다(재도입 의심): {line!r}")


def test_experiments_results_untouched():
    """판정 근거 JSON 3종은 git 추적 파일이어야 하고, 이 태스크의 diff에
    포함되면 안 된다 -- 여기서는 파일 존재만 확인(내용 불변은 Step 6에서
    git diff로 확인)."""
    for name in ("exp4_rag.json", "exp5_persistence.json", "exp5_persistence_primed.json"):
        assert (REPO_ROOT / "experiments" / "results" / name).exists()


def test_report_still_builds_after_neo4j_removal():
    """experiments/report.py:731이 exp4_rag.PRIMING_CALLS를 import한다 --
    SQLite 전용으로 축소하면서 이 상수를 지우면 여기서 ImportError로 죽는다.
    report.build()가 실제로 도는지까지 확인해야 이 회귀를 잡는다(단순
    grep으로는 못 잡는다 -- import 자체는 파일에 남아있어도 상수가
    사라지면 런타임에만 깨진다)."""
    from experiments import report
    text = report.build()
    assert len(text) > 0
