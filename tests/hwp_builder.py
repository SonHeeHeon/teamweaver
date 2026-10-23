"""시험용 옛 한글(HWP 5.x) 파일 만들기 -- 실제 공공 자료는 저장소에 넣지 않으므로(기관 문서) 같은 구조의 작은 파일을 직접 만든다.

복합 문서(CFB v3: 섹터 512, 미니 섹터 64, 미니 기준 4096) + FileHeader + BodyText/Section<n>(문단 글자 레코드). 형제 나무는 오른쪽으로만 잇는다(읽는 쪽은 순서를 보지 않는다).
"""
from __future__ import annotations

import struct
import zlib

MAGIC = bytes.fromhex("D0CF11E0A1B11AE1")
SS, MSS, CUTOFF = 512, 64, 4096
FREE, END, FATSECT, DIFSECT, NOSTREAM = 0xFFFFFFFF, 0xFFFFFFFE, 0xFFFFFFFD, 0xFFFFFFFC, 0xFFFFFFFF
PARA_HEADER, PARA_TEXT = 0x10 + 50, 0x10 + 51
TAB = struct.pack("<8H", 9, 0, 0, 0, 0, 0, 0, 9)                       # 인라인 제어(8칸)
TABLE = struct.pack("<8H", 11, 0x6C62, 0x7420, 0, 0, 0, 0, 11)         # 확장 제어 -- 표(칸 글은 뒤 문단 레코드로)


def deflate(data: bytes) -> bytes:
    c = zlib.compressobj(9, zlib.DEFLATED, -15)
    return c.compress(data) + c.flush()


def para(text: str, *, before: bytes = b"") -> bytes:
    """문단 글자: "\\t" → 탭 제어, "\\n" → 줄바꿈 제어, 끝에 문단 끝(13)."""
    out = bytearray(before)
    for ch in text:
        if ch == "\t":
            out += TAB
        elif ch == "\n":
            out += struct.pack("<H", 10)
        else:
            out += ch.encode("utf-16-le")
    return bytes(out + struct.pack("<H", 13))


def record(tag: int, payload: bytes, level: int = 0) -> bytes:
    if len(payload) >= 0xFFF:
        return struct.pack("<II", tag | level << 10 | 0xFFF << 20, len(payload)) + payload
    return struct.pack("<I", tag | level << 10 | len(payload) << 20) + payload


def section(*paras: bytes) -> bytes:
    """문단마다 문단 머리(잡음 레코드) + 문단 글자."""
    return b"".join(record(PARA_HEADER, b"\x00" * 22) + record(PARA_TEXT, p, 1) for p in paras)


def file_header(props: int) -> bytes:
    return (b"HWP Document File".ljust(32, b"\x00") + struct.pack("<II", 0x05000300, props)).ljust(256, b"\x00")


def hwp(sections: list[bytes], *, compressed: bool = True, props: int = 0, body: str = "BodyText",
        extra: dict[str, bytes] | None = None, **layout) -> bytes:
    streams = {"FileHeader": file_header(props | (1 if compressed else 0)), "DocInfo": deflate(b"\x00" * 40)}
    for i, s in enumerate(sections):
        streams[f"{body}/Section{i}"] = deflate(s) if compressed else s
    streams.update(extra or {})
    return cfb(streams, **layout)


def _entry(name: str, kind: int, left: int, right: int, child: int, start: int, size: int) -> bytes:
    raw = (name.encode("utf-16-le") + b"\x00\x00") if name else b""
    return (raw.ljust(64, b"\x00") + struct.pack("<HBB", len(raw), kind, 1) + struct.pack("<III", left, right, child)
            + b"\x00" * 36 + struct.pack("<IQ", start, size))


def _nodes(streams: dict[str, bytes]) -> list[dict]:
    nodes: list[dict] = [{"name": "Root Entry", "kind": 5, "kids": []}]
    index = {"": 0}
    for path, data in streams.items():
        parts = path.split("/")
        for k in range(1, len(parts)):
            key = "/".join(parts[:k])
            if key not in index:
                index[key] = len(nodes)
                nodes.append({"name": parts[k - 1], "kind": 1, "kids": []})
                nodes[index["/".join(parts[:k - 1])]]["kids"].append(index[key])
        index[path] = len(nodes)
        nodes.append({"name": parts[-1], "kind": 2, "kids": [], "data": data})
        nodes[index["/".join(parts[:-1])]]["kids"].append(index[path])
    return nodes


def cfb(streams: dict[str, bytes], *, cutoff: int = CUTOFF, ss: int = SS, deep: tuple[int, str] | None = None) -> bytes:
    """streams(경로 → 바이트)로 복합 문서를 만든다.
    ss: 섹터 크기(512 = v3, 4096 = v4). FAT 섹터가 109개를 넘으면(섹터 512에서 약 7.1MB 넘는 파일) 나머지 번호는 DIFAT 섹터 사슬에 둔다(규격대로 머리부터 채움).
    · deep: (깊이, 이름) -- 뿌리 아래 저장소를 그 깊이로 한 줄로 잇고 맨 끝에 스트림 하나(경로 문자열 없이 선형으로 만든다)."""
    nodes = _nodes(streams)
    if deep:
        depth, name = deep
        parent = 0
        for _ in range(depth):
            nodes.append({"name": name, "kind": 1, "kids": []})
            nodes[parent]["kids"].append(len(nodes) - 1)
            parent = len(nodes) - 1
        nodes.append({"name": "x", "kind": 2, "kids": [], "data": b"x"})
        nodes[parent]["kids"].append(len(nodes) - 1)
    return _assemble(nodes, cutoff=cutoff, ss=ss)


def _assemble(nodes: list[dict], *, cutoff: int, ss: int) -> bytes:
    epf = ss // 4                                                               # 섹터 하나의 32비트 칸 수
    for n in nodes:
        n.update(left=NOSTREAM, right=NOSTREAM, child=NOSTREAM, start=END, size=0)
    for n in nodes:
        if n["kids"]:
            n["child"] = n["kids"][0]
            for a, b in zip(n["kids"], n["kids"][1:]):
                nodes[a]["right"] = b
    sectors: list[bytes] = []
    fat: list[int] = []

    def alloc(data: bytes) -> int:
        if not data:
            return END
        k, start = (len(data) + ss - 1) // ss, len(sectors)
        for j in range(k):
            sectors.append(data[j * ss:(j + 1) * ss].ljust(ss, b"\x00"))
            fat.append(start + j + 1 if j < k - 1 else END)
        return start

    mini, minifat = bytearray(), []
    for n in nodes:
        if n["kind"] != 2:
            continue
        d = n["data"]
        n["size"] = len(d)
        if not d:
            continue
        if len(d) < cutoff:
            k, start = (len(d) + MSS - 1) // MSS, len(mini) // MSS
            for j in range(k):
                mini += d[j * MSS:(j + 1) * MSS].ljust(MSS, b"\x00")
                minifat.append(start + j + 1 if j < k - 1 else END)
            n["start"] = start
        else:
            n["start"] = alloc(d)
    nodes[0]["size"], nodes[0]["start"] = len(mini), alloc(bytes(mini))
    mfat_start = alloc(struct.pack(f"<{len(minifat)}I", *minifat)) if minifat else END
    n_mfat = (len(minifat) * 4 + ss - 1) // ss
    entries = [_entry(n["name"], n["kind"], n["left"], n["right"], n["child"], n["start"], n["size"]) for n in nodes]
    while len(entries) % (ss // 128):
        entries.append(_entry("", 0, NOSTREAM, NOSTREAM, NOSTREAM, 0, 0))
    dir_start = alloc(b"".join(entries))
    # FAT 섹터 k개 + DIFAT 섹터 m개가 자기 자신까지 덮을 때까지 늘린다
    h = 109
    k = 1
    while True:
        m = -(-max(0, k - h) // (epf - 1))
        if len(sectors) + k + m <= k * epf:
            break
        k += 1
    fat_start = len(sectors)
    difat_start = fat_start + k
    fat += [FATSECT] * k + [DIFSECT] * m
    fat += [FREE] * (k * epf - len(fat))
    for j in range(k):
        sectors.append(struct.pack(f"<{epf}I", *fat[j * epf:(j + 1) * epf]))
    fat_ids = list(range(fat_start, fat_start + k))
    rest = fat_ids[h:]
    for j in range(m):
        chunk = rest[j * (epf - 1):(j + 1) * (epf - 1)]
        nxt = difat_start + j + 1 if j < m - 1 else END
        sectors.append(struct.pack(f"<{epf}I", *(chunk + [FREE] * (epf - 1 - len(chunk)) + [nxt])))
    head_ids = fat_ids[:h] + [FREE] * (109 - len(fat_ids[:h]))
    shift = ss.bit_length() - 1
    head = (MAGIC + b"\x00" * 16 + struct.pack("<HHHHH", 0x3E, 3 if ss == 512 else 4, 0xFFFE, shift, 6) + b"\x00" * 6
            + struct.pack("<IIIIIIIII", 0, k, dir_start, 0, cutoff, mfat_start, n_mfat, difat_start if m else END, m)
            + struct.pack("<109I", *head_ids))
    assert len(head) == 512
    return head.ljust(ss, b"\x00") + b"".join(sectors)
