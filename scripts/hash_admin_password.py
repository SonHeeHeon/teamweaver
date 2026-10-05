"""관리자 비밀번호 해시 만들기(K14).

    uv run python scripts/hash_admin_password.py
    → 나온 줄을 서버 환경변수 TEAMWEAVER_ADMIN_PASSWORD_HASH로 설정한다.

비밀번호는 화면에 보이지 않게 두 번 입력받는다. 해시는 scrypt(무작위 salt)라 같은 비밀번호도
매번 다른 값이 나온다. 평문 TEAMWEAVER_ADMIN_PASSWORD는 로컬 개발용으로만 쓴다.
"""
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from api.admin import hash_password  # noqa: E402


def main() -> int:
    first = getpass.getpass("관리자 비밀번호: ")
    if len(first) < 8:
        print("8자 이상으로 정할 것.", file=sys.stderr)
        return 1
    if getpass.getpass("한 번 더: ") != first:
        print("두 입력이 다르다.", file=sys.stderr)
        return 1
    print(hash_password(first))
    return 0


if __name__ == "__main__":
    sys.exit(main())
