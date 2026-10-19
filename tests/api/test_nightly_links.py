"""야간 배치 연결(claude-b 요청 (3)(5), claude-a 2026-10-19): 미리 계산의 출처 표시, 데이터 전환 뒤 밤 결과 다시 넣기·옛 소명 저장본 정리."""
from types import SimpleNamespace

from api.demo_precomputed import operating_source, precomputed_source


def test_precomputed_source_marks_nightly_results():
    app = SimpleNamespace(state=SimpleNamespace(precomputed_keys={"k1": {"at": "t", "source": "nightly"}, "k2": {"at": "t"}},
                                                precomputed_operating={"key": "x", "source": "nightly"}))
    assert precomputed_source(app, "k1") == "nightly"
    assert precomputed_source(app, "k2") is None and precomputed_source(app, "none") is None
    assert operating_source(app) == "nightly"
    assert operating_source(SimpleNamespace(state=SimpleNamespace())) is None


def test_after_switch_reinstalls_nightly_and_prunes_old_justifications(monkeypatch):
    from api.routes import datasets
    calls = []
    monkeypatch.setattr("api.routes.justification.prune_other_versions", lambda v: calls.append(("prune", v)))

    class Nightly:
        def install_prepared(self):
            calls.append(("install",))
            raise RuntimeError("밤 결과 깨짐")          # 실패해도 전환을 막지 않는다
    app = SimpleNamespace(state=SimpleNamespace(dataset=SimpleNamespace(info=SimpleNamespace(version="v9")), nightly=Nightly()))
    datasets.after_switch(app)
    assert calls == [("prune", "v9"), ("install",)]
    datasets.after_switch(SimpleNamespace(state=SimpleNamespace(dataset=SimpleNamespace(info=SimpleNamespace(version="v1")))))
    assert calls[-1] == ("prune", "v1")


def test_nightly_operating_result_does_not_replace_a_records_k_rows():
    """편성 기록에서 연 데이터의 K 결과(자리 하나)를 밤 결과가 덮지 않는다(폴백 리뷰 P2, 기록 열기·부팅)."""
    from api.nightly import _install_entry
    from api.settings import PlacementSettings
    rec_rows = {"key": "record-key", "rows": [], "source": None}
    dataset = SimpleNamespace(info=SimpleNamespace(version="v1", source="record"))
    app = SimpleNamespace(state=SimpleNamespace(dataset=dataset, precomputed_operating=rec_rows))
    entry = {"kind": "operating", "ks": [0, 1], "key": "x", "milp_params": PlacementSettings().to_milp_params().model_dump()}
    out = _install_entry(app, dataset, entry)
    assert out["status"] == "skipped" and "편성 기록" in out["reason"]
    assert app.state.precomputed_operating is rec_rows
