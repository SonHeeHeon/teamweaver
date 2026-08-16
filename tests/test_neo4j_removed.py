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


def test_no_source_file_imports_neo4j_driver():
    """이 태스크가 실제로 다시 쓰는 두 파일(rehydrate.py, exp5_persistence.py)에
    'neo4j' 문자열이 전혀 남지 않았는지 확인한다.

    주의(브리핑 원문과의 의도적 차이): 브리핑 Step 1 원문은 grep 대상에 core/ 전체,
    experiments/bench/ 전체, decision.py, report.py까지 포함했지만 그대로 실행하면
    구조적으로 항상 실패한다 --
      1) decision.py·report.py는 판정 근거를 설명하는 산문에서 "Neo4j"를 역사적
         사실로 계속 언급한다(각각 38회·159회 매치, 2026-08-16 기준) -- 이 두 파일은
         이 태스크에서 "완전 불변(byte-identical)"이 요구사항이므로 애초에 이
         문자열을 지울 수 없다.
      2) experiments/bench/exp4_rag.py는 브리핑 Step 6이 제공하는 "최종 형태" 코드
         자체가 PRIMING_CALLS 보존 이유를 설명하는 docstring·주석에 "Neo4j"를
         의도적으로 남긴다 -- 같은 브리핑 문서 안에서 재확인 가능하다.
      3) core/graph/memory_graph.py, core/graph/sqlite_store.py, core/rag/queries.py,
         experiments/bench/harness.py, experiments/bench/exp1_storage.py,
         experiments/bench/plots.py는 애초에 이 태스크의 Files 목록에도
         REMOVAL_PLAN 10항목에도 없다 -- 손대지 않는 게 맞고, 그 안의 과거 "Neo4j"
         비교 언급도 그대로 남는 게 맞다(sqlite_store.py는 특히 Task 2 소관).
    그래서 이 테스트는 테스트 이름이 말하는 실제 의도("no_source_file_imports_
    neo4j_driver")를 좇아, 이 태스크가 실제로 SQLite 전용으로 다시 쓰는 두 파일만
    검사 대상으로 좁힌다. exp5_persistence.py는 Step 7 본문이 별도로
    "grep -n neo4j ... 가 빈 결과를 내야 한다"고 명시하므로 이 좁혀진 범위와도
    모순이 없다. (판단 근거 상세는 .omc/reports/2026-08-16-plan4-task1-neo4j-removal.md
    "판단이 필요했던 지점" 참고.)"""
    result = subprocess.run(
        ["grep", "-riln", "neo4j",
         "core/graph/rehydrate.py", "experiments/bench/exp5_persistence.py"],
        cwd=REPO_ROOT, capture_output=True, text=True)
    # grep exit code 1 = no matches found (원하는 결과). 0 = 매치 있음(실패).
    assert result.returncode == 1, f"neo4j 문자열이 남아있는 파일: {result.stdout}"


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
