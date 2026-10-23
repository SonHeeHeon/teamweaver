"""옛 한글(HWP 5.x) 글자 읽기(2026-10-23 사용자 결정 "HWP 읽기 추가", `api/hwp5.py`).

실제 공공 자료(나라장터 사전규격 첨부)는 기관 문서라 저장소에 넣지 않는다 -- 같은 구조의 작은 파일을 `tests/hwp_builder`로 만든다.
실제 파일 2편(B 소상공인시장진흥공단·C 한국산업안전보건공단)은 손으로 확인했다(리포트 `.omc/reports/2026-10-23-real-bid-docs.md`).
"""
import struct
import time
import zlib

import pytest

from tests import hwp_builder as hb


def _store():
    import api.project_docs as store
    return store


def _numbered(n: int) -> str:
    return "\n".join(f"항목 {i} 값 {i * 7919 % 10007}" for i in range(n))     # 잘 눌리지 않는 글(큰 스트림 = 일반 섹터 사슬)


def test_reads_paragraphs_tables_controls_and_sections_in_order():
    """표 칸은 뒤 문단 레코드로 나와 읽는 순서 그대로, 탭·줄바꿈·고정폭 빈칸, 확장 문자(대리 쌍), 섹션은 번호 순(Section10이 Section2 뒤)."""
    s0 = hb.section(hb.para("필요 기술:\tJava 5년 이상\nOracle 3년"), hb.para("표 앞", before=hb.TABLE), hb.para("ETL 2명"),
                    hb.para("표 뒤"), hb.para("용역명\x1f:\x1f생성형 AI 𝔸"))
    rest = [hb.section(hb.para(f"섹션 {i}")) for i in range(1, 11)]
    text = _store().extract_text("hwp", hb.hwp([s0, *rest]))
    lines = text.split("\n")
    assert lines[:6] == ["필요 기술: Java 5년 이상", "Oracle 3년", "표 앞", "ETL 2명", "표 뒤", "용역명 : 생성형 AI 𝔸"]
    assert lines[6:] == [f"섹션 {i}" for i in range(1, 11)]


def test_reads_small_and_big_streams_compressed_or_not():
    """4KiB 미만 스트림은 미니 스트림, 큰 스트림은 일반 섹터 사슬 -- 둘 다, 압축이든 아니든."""
    big = _numbered(3000)
    for compressed in (True, False):
        data = hb.hwp([hb.section(hb.para("짧은 섹션")), hb.section(hb.para(big))], compressed=compressed)
        text = _store().extract_text("hwp", data)
        assert text.startswith("짧은 섹션\n항목 0 값 0") and text.endswith(big.rsplit("\n", 1)[1])
    assert len(hb.deflate(hb.section(hb.para(big)))) > hb.CUTOFF                 # 큰 스트림 경로를 정말 탔다


def test_reads_v4_sectors_and_difat_chains():
    """섹터 4096(v4)과 DIFAT 사슬(FAT 섹터 109개 밖의 번호) -- 폴백 리뷰 N3: 만드는 쪽이 못 만들어 시험이 닿지 않았다.
    DIFAT: 약 7.5MB 비압축 섹션(문단 글자가 아닌 레코드 하나) 뒤에 미니 섹션 -- 미니 스트림 섹터는 파일 끝이라 그 FAT 칸은 DIFAT이 가리키는 FAT 섹터에 있다."""
    big = _numbered(3000)
    data = hb.hwp([hb.section(hb.para("앞")), hb.section(hb.para(big))], compressed=False, ss=4096)
    assert struct.unpack_from("<H", data, 0x1E)[0] == 12
    assert _store().extract_text("hwp", data) == "앞\n" + big
    filler = hb.record(0x50, b"\x00" * 7_500_000)
    data = hb.hwp([filler, hb.section(hb.para("끝 표지 ETL 2명"))], compressed=False)
    assert struct.unpack_from("<II", data, 0x2C)[0] > 109 and struct.unpack_from("<I", data, 0x48)[0] >= 1   # FAT 섹터 109개 초과 · DIFAT 섹터 있음
    assert _store().extract_text("hwp", data) == "끝 표지 ETL 2명"


def test_deep_storage_chain_is_not_walked():
    """뿌리 아래 저장소를 4,000단 이은 파일(약 0.5MB, 이름 31자) -- 예전엔 경로 문자열이 깊이²로 커져 +545MB였다(폴백 리뷰 M1).
    HWP가 쓰는 경로는 깊이 2라 뿌리 바로 아래 저장소까지만 내려간다."""
    import tracemalloc
    data = hb.cfb({"FileHeader": hb.file_header(1), "BodyText/Section0": hb.deflate(hb.section(hb.para("ETL 2명")))},
                  deep=(4000, "가" * 31))
    tracemalloc.start()
    t = time.monotonic()
    try:
        text = _store().extract_text("hwp", data)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert text == "ETL 2명"
    assert time.monotonic() - t < 2.0 and peak < 30 * 1024 * 1024, peak


def test_stops_near_the_text_limit():
    store = _store()
    data = hb.hwp([hb.section(*[hb.para("가" * 1000) for _ in range(400)])])     # 40만 자
    text = store.extract_text("hwp", data)
    assert len(text) == store.MAX_TEXT_CHARS


@pytest.mark.parametrize("props, why", [(2, "암호"), (4, "배포용"), (1 << 4, "보안"), (1 << 8, "보안"), (1 << 10, "보안")])
def test_password_and_distribution_documents_say_why(props, why):
    with pytest.raises(_store().DocError, match=why):
        _store().extract_text("hwp", hb.hwp([hb.section(hb.para("x"))], props=props))


def test_not_hwp_or_broken_files_are_doc_errors():
    store = _store()
    no_header = hb.cfb({"WordDocument": b"x" * 100})                             # 다른 복합 문서(옛 워드 등)를 이름만 바꾼 것
    wrong_sig = hb.cfb({"FileHeader": b"Not a HWP".ljust(256, b"\x00"), "BodyText/Section0": b""})
    no_body = hb.cfb({"FileHeader": hb.file_header(1)})
    garbage = hb.cfb({"FileHeader": hb.file_header(1), "BodyText/Section0": b"\xff" * 300})
    for data, why in [(hb.MAGIC + b"0" * 10, "한글"), (hb.MAGIC + b"\x00" * 600, "한글"), (no_header, "머리 정보"),
                      (wrong_sig, "서명"), (no_body, "본문"), (garbage, "압축")]:
        with pytest.raises(store.DocError, match=why):
            store.extract_text("hwp", data)
    data = bytearray(hb.hwp([hb.section(hb.para("ETL 2명"))]))
    for pos in range(512, len(data), 37):                                       # 바이트를 하나씩 망가뜨려도 DocError 밖의 예외는 없다
        broken = bytearray(data)
        broken[pos] ^= 0xFF
        try:
            store.extract_text("hwp", bytes(broken))
        except store.DocError:
            pass


def test_truncated_compressed_body_keeps_what_was_read():
    sec = hb.section(*[hb.para(f"문단 {i}") for i in range(2000)])
    z = hb.deflate(sec)
    data = hb.cfb({"FileHeader": hb.file_header(1), "BodyText/Section0": z[: len(z) // 2]})
    text = _store().extract_text("hwp", data)
    assert text.startswith("문단 0\n문단 1") and "문단 1999" not in text


def test_cycles_in_sector_chains_and_directory_do_not_hang():
    """조작한 파일: 모든 사슬 끝을 자기 자신으로, 디렉터리 형제·자식이 뿌리·부모·형제로 되돌아가게 -- 방문 집합으로 멈추고 글은 그대로 읽는다.
    항목 번호: 0 뿌리 · 1 FileHeader · 2 DocInfo · 3 BodyText · 4 Section0 · 5 Section1(형제는 오른쪽으로 이어짐)."""
    data = bytearray(hb.hwp([hb.section(hb.para("ETL 2명")), hb.section(hb.para(_numbered(3000)))]))
    fat_sec = struct.unpack_from("<I", data, 0x4C)[0]
    base = (fat_sec + 1) * hb.SS
    for i in range(hb.SS // 4):
        if struct.unpack_from("<I", data, base + 4 * i)[0] == hb.END:
            struct.pack_into("<I", data, base + 4 * i, i)
    entry = (struct.unpack_from("<I", data, 0x30)[0] + 1) * hb.SS              # 디렉터리 섹터는 이어져 있다(만들 때 차례로 잡음)
    struct.pack_into("<I", data, entry + 1 * 128 + 0x44, 0)                    # FileHeader 왼쪽 = 뿌리
    struct.pack_into("<I", data, entry + 4 * 128 + 0x44, 3)                    # Section0 왼쪽 = 부모 저장소
    struct.pack_into("<I", data, entry + 5 * 128 + 0x48, 4)                    # Section1 오른쪽 = 앞 형제
    struct.pack_into("<I", data, entry + 3 * 128 + 0x48, 3)                    # BodyText 오른쪽 = 자기
    t = time.monotonic()
    text = _store().extract_text("hwp", bytes(data))
    assert time.monotonic() - t < 2.0
    assert text.startswith("ETL 2명")


def test_decompression_bomb_is_capped_and_quick():
    """0으로 된 64MiB를 눌러 넣은 섹션(약 64KB) -- 압축 해제 상한에서 멈추고 몇 초 안에 끝난다."""
    from api import hwp5
    c = zlib.compressobj(9, zlib.DEFLATED, -15)
    zero = b"\x00" * (1 << 20)
    bomb = b"".join(c.compress(zero) for _ in range(64)) + c.flush()
    data = hb.cfb({"FileHeader": hb.file_header(1), "BodyText/Section0": bomb, "BodyText/Section1": hb.deflate(hb.section(hb.para("뒤")))})
    t = time.monotonic()
    text = _store().extract_text("hwp", data)
    assert time.monotonic() - t < 5.0
    assert text == ""                                                          # 상한을 다 써서 뒤 섹션까지 가지 않는다
    assert hwp5.MAX_INFLATED < 64 * (1 << 20)


def test_limited_runs_hwp_in_the_child_process():
    data = hb.hwp([hb.section(hb.para("ETL 경력 5년 이상 2명"))])
    assert _store().extract_text_limited("hwp", data) == "ETL 경력 5년 이상 2명"
    with pytest.raises(_store().DocError, match="배포용"):
        _store().extract_text_limited("hwp", hb.hwp([hb.section(hb.para("x"))], props=4))
