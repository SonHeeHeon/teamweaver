"""생성된 가상 데이터에 실제 회사 이름이 섞이지 않았는지 검사한다 -- 이름 자체는 저장소에 적지 않고 지문(키 있는 해시)만 둔다.

금지 이름은 모두 한글만 또는 영문만으로 된 낱말이라, 글에서 한글 덩어리·영문 덩어리를 뽑아(중복 제거) 그 안의 같은 길이
구간만 지문과 대조하면 빠짐없이 찾는다. 대소문자는 구분한다(예전 문자열 검사와 같은 뜻). 지문을 더하려면
`fingerprint(낱말)` 값을 FINGERPRINTS에 (글자 종류, 길이, 값)으로 넣는다.
"""
from __future__ import annotations

import hashlib
import re

_KEY = b"teamweaver/no-real-client-names"
_RUNS = {"hangul": re.compile(r"[가-힣]+"), "latin": re.compile(r"[A-Za-z]+")}

FINGERPRINTS: frozenset[tuple[str, int, str]] = frozenset({
    ("hangul", 4, "a75e0e454be9c229c99972208b529af5"),
    ("hangul", 2, "44a858724149a4bed1b17e99c637b1d5"),
    ("latin", 7, "5de8fb630cece338fa91c1d5d2de4bad"),
    ("latin", 5, "3f28b21ae7cf25813863b51bbcb318d5"),
})


def fingerprint(word: str) -> str:
    return hashlib.blake2b(word.encode("utf-8"), digest_size=16, key=_KEY).hexdigest()


def banned_hits(text: str, fingerprints=FINGERPRINTS) -> list[str]:
    """text 안에서 지문과 맞는 구간들(없으면 빈 목록)."""
    hits = []
    for script, pattern in _RUNS.items():
        wanted = {(n, h) for s, n, h in fingerprints if s == script}
        if not wanted:
            continue
        lengths = {n for n, _ in wanted}
        for run in set(pattern.findall(text)):
            for n in lengths:
                for i in range(len(run) - n + 1):
                    piece = run[i:i + n]
                    if (n, fingerprint(piece)) in wanted:
                        hits.append(piece)
    return hits
