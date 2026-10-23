"""옛 한글(HWP 5.x) 본문 글자 꺼내기(2026-10-23 사용자 결정 "HWP 읽기 추가", claude-b).

나라장터 사전규격·제안요청서 첨부는 대부분 옛 한글 파일이다(사용자가 받아 온 실제 자료 3편 중 2편). 새 의존성 없이 표준 라이브러리로 읽는다:
- 파일 = 복합 문서(OLE/CFB, "작은 파일 시스템"): 머리 → FAT(섹터 사슬 표) → 디렉터리(이름 나무) → 스트림. 4KiB 미만 스트림은 미니 스트림에 있다.
- `FileHeader` 스트림: 서명 "HWP Document File", 속성 비트 0 압축 · 1 암호 · 2 배포용(보기 전용 -- 본문이 `ViewText`에 암호화돼 있다).
- `BodyText/Section<n>` 스트림: (압축이면 raw deflate) 레코드 열. 레코드 머리 32비트 = 태그 10 · 단계 10 · 크기 12(0xFFF면 다음 32비트가 크기).
  문단 글자 레코드(HWPTAG_PARA_TEXT = 0x10 + 51)는 UTF-16LE이고, 0~31은 제어 문자다 -- 글자 제어(1칸: 0·10 줄바꿈·13 문단 끝·24~31)와
  인라인·확장 제어(8칸: 탭 9·표·그림·필드 등 -- 내용은 뒤 레코드의 문단으로 따로 나온다).
- 표 칸·글상자 글도 같은 섹션의 문단 레코드라 읽는 순서대로 나온다.

남이 만든 파일이다: `api.project_docs.extract_text_limited`의 별도 프로세스·시간 제한 안에서 불린다. 여기서도 섹터 사슬·디렉터리 나무 순환을 방문 집합으로 끊고,
범위 밖 번호에서 멈추고, 스트림 읽기 합계·압축 해제 합계·글자 수에 상한을 둔다. 실패는 HwpError 하나로만 알린다.
디렉터리는 뿌리 바로 아래 저장소까지만 내려간다(HWP가 쓰는 경로는 깊이 2 -- 깊게 이은 저장소에서 경로 문자열이 깊이²로 커져
1MB 파일이 0.4초에 1.2GB를 썼다, 폴백 리뷰 M1). FAT 섹터 번호는 필요한 개수까지만 모은다(S1).
"""
from __future__ import annotations

import struct
import sys
import zlib
from array import array

_MAGIC = bytes.fromhex("D0CF11E0A1B11AE1")
_SIGNATURE = b"HWP Document File"
_SPECIAL = 0xFFFFFFFA            # 이 값 이상의 섹터 번호는 사슬 끝·빈 칸·FAT 섹터 표시
_NOSTREAM = 0xFFFFFFFF
_PARA_TEXT = 0x10 + 51
_SECURED = (1 << 4) | (1 << 8) | (1 << 10)      # FileHeader 속성: DRM 보안 · 공인인증서 암호화 · 공인인증서 DRM(본문이 암호화돼 있다, 폴백 리뷰 N2)
_MAX_SECTIONS = 1024
# 섹션 압축 해제 합계(압축 폭탄 막기). 실제 자료 2편은 글자당 8~10바이트(2.1만 자 22만 바이트) -- 20만 자 ≈ 2MB의 4배.
# 0만 든 본문은 4바이트 빈 레코드가 이어져 Python 반복이 상한/4번 돈다(8MiB ≈ 200만 번).
MAX_INFLATED = 8 * 1024 * 1024
_CHUNK = 1 << 16
# 1칸 글자 제어 → 글자(그 밖의 1칸 제어는 버린다)
_CHAR_CONTROLS = {10: "\n", 13: "\n", 24: "-", 30: " ", 31: " "}


class HwpError(ValueError):
    """읽을 수 없는 한글 파일(깨짐·암호·배포용·한글 문서 아님) -- 문장은 화면에 그대로 보인다."""


def _u32s(raw: bytes) -> array:
    a = array("I")
    a.frombytes(raw[: len(raw) // 4 * 4])
    if sys.byteorder == "big":
        a.byteswap()
    return a


class _Cfb:
    """복합 문서 읽기 -- 필요한 스트림만 이름으로 꺼낸다."""

    def __init__(self, data: bytes):
        if len(data) < 512 or data[:8] != _MAGIC:
            raise HwpError("한글(HWP) 문서가 아니다 -- 복합 문서 머리가 없다")
        shift, mshift = struct.unpack_from("<HH", data, 0x1E)
        if shift not in (9, 12) or mshift != 6:
            raise HwpError("한글(HWP) 문서가 아니다 -- 섹터 크기가 다르다")
        self.data = data
        self.ss, self.mss = 1 << shift, 1 << mshift
        self.n_sectors = max(0, (len(data) - self.ss) // self.ss)      # 섹터 n은 (n+1)*ss에서 시작한다(머리가 섹터 하나)
        n_fat, dir_start = struct.unpack_from("<II", data, 0x2C)
        self.cutoff, mfat_start, _, difat_start, n_difat = struct.unpack_from("<IIIII", data, 0x38)
        # FAT 섹터 번호: 머리 109개 + DIFAT 사슬 -- FAT는 섹터 수만큼이면 되므로 그 개수까지만 모은다(조작한 칸 수·DIFAT 사슬로 메모리를 키우지 않게)
        need = min(n_fat, self.n_sectors // (self.ss // 4) + 1)
        fat_ids = _u32s(data[0x4C:0x4C + 109 * 4])
        d, seen = difat_start, set()
        for _ in range(min(n_difat, self.n_sectors)):
            if len(fat_ids) >= need or d >= self.n_sectors or d in seen:
                break
            seen.add(d)
            vals = _u32s(self._sector(d))
            fat_ids.extend(vals[:-1])
            d = vals[-1]
        self.fat = array("I")
        for f in fat_ids[:need]:
            if f >= self.n_sectors or len(self.fat) >= self.n_sectors + self.ss // 4:
                break                                               # FAT는 섹터 수보다 길 필요가 없다(조작한 칸 수로 메모리를 키우지 않게)
            self.fat.extend(_u32s(self._sector(f)))
        self.entries = self._directory(self._read_chain(dir_start, self.fat, self.n_sectors, self._sector))
        if not self.entries or self.entries[0][1] != 5:
            raise HwpError("한글(HWP) 문서가 아니다 -- 뿌리 항목이 없다")
        root = self.entries[0]
        self.ministream = self._read_chain(root[5], self.fat, self.n_sectors, self._sector)[: root[6]]
        n_mini = len(self.ministream) // self.mss
        self.minifat = _u32s(self._read_chain(mfat_start, self.fat, self.n_sectors, self._sector)[: n_mini * 4 + 4])
        self.paths = self._walk()

    def _sector(self, n: int) -> bytes:
        return self.data[(n + 1) * self.ss:(n + 2) * self.ss]

    def _mini(self, n: int) -> bytes:
        return self.ministream[n * self.mss:(n + 1) * self.mss]

    @staticmethod
    def _read_chain(start: int, table: array, count: int, get, limit: int | None = None) -> bytes:
        """사슬을 따라 읽는다 -- 범위 밖·특수 번호·이미 본 번호(순환)에서 멈춘다. limit 바이트를 넘으면 그만 읽는다."""
        out, n, seen, size = [], start, set(), 0
        while n < _SPECIAL and n < count and n < len(table) and n not in seen:
            seen.add(n)
            chunk = get(n)
            out.append(chunk)
            size += len(chunk)
            if limit is not None and size >= limit:
                break
            n = table[n]
        return b"".join(out)

    @staticmethod
    def _directory(raw: bytes) -> list[tuple]:
        """(이름, 종류, 왼쪽, 오른쪽, 자식, 시작 섹터, 크기). 종류 1 저장소 · 2 스트림 · 5 뿌리."""
        out = []
        for i in range(len(raw) // 128):
            e = raw[i * 128:(i + 1) * 128]
            nl = min(struct.unpack_from("<H", e, 0x40)[0], 64)
            name = e[:max(nl - 2, 0)].decode("utf-16-le", "replace")
            left, right, child = struct.unpack_from("<III", e, 0x44)
            start, size = struct.unpack_from("<II", e, 0x74)            # 크기 위 32비트는 v3에서 쓰레기일 수 있어 보지 않는다
            out.append((name, e[0x42], left, right, child, start, size))
        return out

    def _walk(self) -> dict[str, int]:
        """경로("BodyText/Section0") → 항목 번호. 형제 나무를 반복문 + 방문 집합으로 돈다(조작한 순환·깊은 나무에서 멈추지 않게)."""
        paths: dict[str, int] = {}
        stack, seen = [(self.entries[0][4], "")], set()
        while stack:
            i, prefix = stack.pop()
            if i == _NOSTREAM or i >= len(self.entries) or i in seen:
                continue
            seen.add(i)
            name, kind, left, right, child = self.entries[i][:5]
            stack.append((left, prefix))
            stack.append((right, prefix))
            if kind == 2:
                paths.setdefault(prefix + name, i)
            elif kind == 1 and not prefix:                     # 뿌리 바로 아래 저장소만(BodyText) -- 더 깊은 경로는 쓰지 않는다
                stack.append((child, f"{name}/"))
        return paths

    def stream(self, path: str, budget: int) -> bytes:
        """스트림 바이트(크기까지, 최대 budget 바이트)."""
        _, _, _, _, _, start, size = self.entries[self.paths[path]]
        want = min(size, budget)
        if size < self.cutoff:
            return self._read_chain(start, self.minifat, len(self.ministream) // self.mss, self._mini, want)[:want]
        return self._read_chain(start, self.fat, self.n_sectors, self._sector, want)[:want]


def _inflate(raw: bytes, room: int) -> tuple[bytes, bool]:
    """raw deflate 풀기, 최대 room 바이트. 중간이 깨지면 그때까지 푼 것과 실패 표시(잘린 파일도 앞 글은 남긴다)."""
    d = zlib.decompressobj(-15)
    out = bytearray()
    try:
        for i in range(0, len(raw), _CHUNK):
            left = room - len(out)
            if left <= 0:
                break
            out += d.decompress(raw[i:i + _CHUNK], left)      # max_length 0은 "제한 없음"이라 left > 0일 때만 부른다
            if d.unconsumed_tail or d.eof:
                break
    except zlib.error:
        return bytes(out), True
    return bytes(out), False


def _para_text(raw: bytes) -> str:
    units = array("H")
    units.frombytes(raw[: len(raw) // 2 * 2])
    if sys.byteorder == "big":
        units.byteswap()
    keep = array("H")
    i, n = 0, len(units)
    while i < n:
        c = units[i]
        if c >= 32:
            keep.append(c)
            i += 1
        elif c in (0, 10, 13) or 24 <= c <= 31:                # 1칸 글자 제어
            rep = _CHAR_CONTROLS.get(c)
            if rep:
                keep.append(ord(rep))
            i += 1
        else:                                                   # 인라인·확장 제어는 8칸(제어 문자 + 정보 6칸 + 제어 문자)
            if c == 9:
                keep.append(0x20)
            i += 8
    if sys.byteorder == "big":
        keep.byteswap()
    return keep.tobytes().decode("utf-16-le", "replace")       # 대리 쌍은 한 글자로, 짝 없는 대리 문자는 U+FFFD로


def _records_text(body: bytes, out: list[str], room: int) -> int:
    """섹션 레코드 열에서 문단 글자만 꺼내 out에 더한다 → 더한 글자 수(room을 넘으면 그 문단까지)."""
    i, got, n = 0, 0, len(body)
    while i + 4 <= n and got < room:
        (head,) = struct.unpack_from("<I", body, i)
        i += 4
        tag, size = head & 0x3FF, head >> 20
        if size == 0xFFF:
            if i + 4 > n:
                break
            (size,) = struct.unpack_from("<I", body, i)
            i += 4
        if tag == _PARA_TEXT:
            text = _para_text(body[i:i + size])
            out.append(text)
            got += len(text)
        i += size
    return got


def hwp_text(data: bytes, max_chars: int, info: dict | None = None) -> str:
    """옛 한글 파일의 본문 글자(대략 앞 max_chars자 -- 문단 단위로 끊으므로 조금 넘을 수 있다). 실패는 HwpError만.
    info를 주면 상한(글자·압축 해제·읽기 합계·섹션 수)에서 끊었을 때 info["truncated"] = True."""
    try:
        return _hwp_text(data, max_chars, info if info is not None else {})
    except HwpError:
        raise
    except Exception as exc:          # noqa: BLE001 -- 남이 만든 파일: 어떤 해석 오류든 "읽지 못함"으로만 알린다
        raise HwpError(f"한글 문서를 읽지 못했다: {type(exc).__name__}") from exc


def _hwp_text(data: bytes, max_chars: int, info: dict) -> str:
    cfb = _Cfb(data)
    if "FileHeader" not in cfb.paths:
        raise HwpError("한글(HWP) 문서가 아니다 -- 파일 머리 정보가 없다")
    head = cfb.stream("FileHeader", 256)
    if len(head) < 40 or not head.startswith(_SIGNATURE):
        raise HwpError("한글(HWP) 문서가 아니다 -- 한글 서명이 없다")
    (props,) = struct.unpack_from("<I", head, 36)
    if props & 2:
        raise HwpError("암호가 걸린 한글 문서라 글자를 읽지 못한다 -- 한글에서 암호를 풀거나 PDF로 저장해 올린다")
    if props & 4:
        raise HwpError("배포용(보기 전용) 한글 문서라 글자를 읽지 못한다 -- 한글에서 PDF로 저장해 올린다")
    if props & _SECURED:
        raise HwpError("보안(DRM)·인증서로 암호화된 한글 문서라 글자를 읽지 못한다 -- 한글에서 PDF로 저장해 올린다")
    compressed = bool(props & 1)
    sections = sorted((p for p in cfb.paths if p.startswith("BodyText/Section") and p[16:].isascii() and p[16:].isdigit()),
                      key=lambda p: int(p[16:]))
    if len(sections) > _MAX_SECTIONS:
        info["truncated"] = True
        sections = sections[:_MAX_SECTIONS]
    if not sections:
        raise HwpError("한글 문서에서 본문을 찾지 못했다 -- 한글에서 PDF로 저장해 올린다")
    out: list[str] = []
    chars, inflated, read, broken = 0, 0, 0, False
    read_budget = 4 * len(data)                                 # 스트림 읽기 합계 -- 여러 섹션이 같은 섹터를 가리키게 조작해도 파일 몇 배까지만
    for p in sections:
        if chars >= max_chars or inflated >= MAX_INFLATED or read >= read_budget:
            info["truncated"] = True                # 뒤 섹션을 읽지 않았다
            break
        raw = cfb.stream(p, read_budget - read)
        read += len(raw)
        if compressed:
            body, bad = _inflate(raw, MAX_INFLATED - inflated)
            broken |= bad
        else:
            body = raw[: MAX_INFLATED - inflated]
        inflated += len(body)
        chars += _records_text(body, out, max_chars - chars)
        if chars >= max_chars or inflated >= MAX_INFLATED:
            info["truncated"] = True                # 이 섹션 안에서 글자·압축 해제 상한에 닿았다
    text = "".join(out)
    if broken and not text.strip():
        raise HwpError("한글 문서 본문의 압축을 풀지 못했다(깨진 파일)")
    return text
