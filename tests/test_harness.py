import json
import os
import time
from pathlib import Path

from experiments.bench import harness, datasets


def test_measure_shape_and_ordering():
    r = harness.measure(lambda: time.sleep(0.001), repeats=5, warmup=2)
    assert {"median_ms", "p95_ms", "min_ms", "max_ms", "repeats", "warmup"} <= r.keys()
    assert r["repeats"] == 5 and r["warmup"] == 2
    assert r["min_ms"] <= r["median_ms"] <= r["p95_ms"] <= r["max_ms"]
    assert r["median_ms"] >= 0.5     # 1ms sleep 이므로 하한만 느슨하게 검증


def test_measure_excludes_warmup_from_stats():
    calls = []
    def fn():
        calls.append(1)
        time.sleep(0.02 if len(calls) <= 3 else 0.001)   # 웜업 3회만 느리게
    r = harness.measure(fn, repeats=4, warmup=3)
    assert len(calls) == 7                      # 웜업 3 + 측정 4
    assert r["max_ms"] < 15.0, "웜업(20ms)이 통계에 섞이면 실패한다"


def test_environment_records_versions():
    env = harness.environment()
    assert env["python"].startswith("3.12")
    for key in ("platform", "cpu_count", "packages", "cbc"):
        assert key in env
    assert "numpy" in env["packages"]


def test_environment_records_neo4j_server_cpu_and_ram():
    """최종 리뷰 Important 9: 헤드라인이 'Neo4j가 5배 느리다'인 벤치마크인데
    environment()가 Neo4j *서버* 버전(클라이언트 드라이버 버전과 별개)도, CPU
    모델·RAM도 기록하지 않았다. 세 값 모두 방어적으로 수집돼야 하고(probe 실패가
    벤치마크 자체를 깨면 안 됨), 실패 시에도 문자열 키는 항상 존재해야 한다."""
    env = harness.environment()
    for key in ("neo4j_server", "cpu_model", "ram"):
        assert key in env
        assert isinstance(env[key], str) and env[key]


def test_neo4j_server_version_never_raises_when_driver_module_is_absent():
    """`_neo4j_server_version`은 아직 harness.py에 남아 있다(Plan 4 Task 1의
    REMOVAL_PLAN 10항목에 experiments/bench/harness.py는 없다 -- 그 파일이
    exp1_storage.py와 함께 이번 태스크의 범위 밖이라는 근거는
    .omc/reports/2026-08-16-plan4-task1-neo4j-removal.md 참고). 이 probe는
    `from core.graph.neo4j_store import get_driver`를 함수 안에서 지연
    임포트하는데, 그 모듈이 이제 삭제됐으므로 매번 ModuleNotFoundError를
    맞는다 -- 원래 테스트가 몽키패치로 흉내 내려던 "probe가 실패한다"는
    상황을 코드 변경 없이 이미 항상 재현한다. probe는 항상 방어적이어야
    하므로(environment() 전체가 죽으면 안 된다) 여기서도 예외 없이 문자열을
    돌려줘야 한다."""
    result = harness._neo4j_server_version()
    assert isinstance(result, str) and "unavailable" in result


def test_save_and_load_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(harness, "RESULTS_DIR", tmp_path)
    p = harness.save_result("demo", {"a": 1})
    assert p.exists()
    loaded = harness.load_result("demo")
    assert loaded["data"] == {"a": 1} and "environment" in loaded


def test_save_result_uses_same_directory_atomic_replace(tmp_path, monkeypatch):
    _fresh_run(tmp_path, monkeypatch)
    replace_calls = []
    real_replace = os.replace

    def recording_replace(source, destination):
        replace_calls.append((Path(source), Path(destination)))
        real_replace(source, destination)

    monkeypatch.setattr(harness.os, "replace", recording_replace)

    path = harness.save_result("phase0", {"case": 1})

    assert path == tmp_path / "phase0.json"
    assert replace_calls == [(replace_calls[0][0], path)]
    assert replace_calls[0][0].parent == tmp_path
    assert replace_calls[0][0] != path


def _fresh_run(tmp_path, monkeypatch):
    """새 프로세스에서 러너를 돌리는 것과 같은 상태로 만든다."""
    monkeypatch.setattr(harness, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr(harness, "_WRITTEN_THIS_RUN", set())


def test_save_result_preserves_a_previous_runs_file(tmp_path, monkeypatch):
    """최종 리뷰 T4-c: save_result가 고정 경로에 그대로 쓰는 바람에 exp4의 첫
    스윕을 복구 불가능하게 잃은 전례가 있다. 다음 실험이 같은 손실을 반복하지
    못하도록, 이전 **실행**의 파일은 덮어쓰기 전에 옆으로 옮겨 보존한다."""
    _fresh_run(tmp_path, monkeypatch)
    harness.save_result("sweep", {"run": 1})

    _fresh_run(tmp_path, monkeypatch)                       # 두 번째 실행
    harness.save_result("sweep", {"run": 2})

    assert harness.load_result("sweep")["data"] == {"run": 2}
    kept = tmp_path / "sweep.superseded-1.json"
    assert kept.exists(), "이전 실행의 원자료가 사라졌다"
    assert json.loads(kept.read_text("utf-8"))["data"] == {"run": 1}


def test_save_result_preserves_every_previous_run_not_just_the_last(tmp_path, monkeypatch):
    """보존 파일 자체도 덮어쓰지 않는다 — 세 번 돌리면 두 개가 남는다."""
    for i in (1, 2, 3):
        _fresh_run(tmp_path, monkeypatch)
        harness.save_result("sweep", {"run": i})
    assert harness.load_result("sweep")["data"] == {"run": 3}
    assert json.loads((tmp_path / "sweep.superseded-1.json").read_text("utf-8"))["data"] == {"run": 1}
    assert json.loads((tmp_path / "sweep.superseded-2.json").read_text("utf-8"))["data"] == {"run": 2}


def test_save_result_does_not_back_up_its_own_run(tmp_path, monkeypatch):
    """exp3는 스케일마다 같은 이름으로 체크포인트를 쓴다 — 자기 자신을 덮어쓰는
    것이므로 백업하지 않는다. 안 그러면 체크포인트마다 파일이 쌓여 정작 지켜야 할
    이전 실행의 원자료가 노이즈에 묻힌다."""
    _fresh_run(tmp_path, monkeypatch)
    for i in (1, 2, 3):
        harness.save_result("checkpointed", {"scale": i})
    assert harness.load_result("checkpointed")["data"] == {"scale": 3}
    assert list(tmp_path.glob("*.superseded-*.json")) == []


def test_save_result_never_refuses_so_existing_runners_keep_working(tmp_path, monkeypatch):
    """보존이지 거부가 아니다. 러너를 못 돌게 만드는 가드는 우회되거나 러너를 안
    고치게 만든다 — exp1~exp5 전부 재실행이 그대로 성공해야 한다."""
    for name in ("exp1_storage", "exp2_pipeline", "exp3_algorithm",
                 "exp4_rag", "exp5_persistence"):
        for run in (1, 2):
            _fresh_run(tmp_path, monkeypatch)
            path = harness.save_result(name, {"rows": [], "run": run})   # raise하면 실패
            assert path.exists()


def test_build_scale_shapes():
    ds, parsed, g = datasets.build_scale(50, 10, seed=42)
    assert len(ds.people) == 50 and len(ds.projects) == 10
    assert len(parsed) == len(ds.reviews)
    assert g.cowork_months.shape == (50, 50)


def test_scales_ratio_is_five_to_one():
    assert datasets.SCALES[0] == (50, 10) and datasets.SCALES[-1] == (1000, 200)
    assert all(n // 5 == j for n, j in datasets.SCALES)
