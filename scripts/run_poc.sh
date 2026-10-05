#!/usr/bin/env bash
# TeamWeaver PoC 실행: 소스 체크아웃을 그대로 한 포트로 띄운다(API + 웹 화면 + PDF).
# 배포 단위는 "소스 그대로"로 정했다(2026-10-05 사용자 결정 -- PoC라 가장 간단한 방법).
#
#   scripts/run_poc.sh                    # 준비(의존성·웹 빌드·Chromium) 후 127.0.0.1:8000
#   HOST=0.0.0.0 PORT=8080 scripts/run_poc.sh
#   SKIP_SETUP=1 scripts/run_poc.sh       # 준비 단계 생략(이미 한 번 돌렸을 때)
#
# 저장소 루트의 .env(OPENAI_API_KEY=...)가 있으면 읽는다. 키가 없으면 브리핑은 규칙 기반으로 나온다.
# 다른 사람이 접속하는 주소(HOST=0.0.0.0)로 띄울 때는 관리자 비밀번호를 정한다:
#   TEAMWEAVER_ADMIN_PASSWORD_HASH="$(uv run python scripts/hash_admin_password.py)"
# 데이터(업로드 묶음·설정·서명키)는 TEAMWEAVER_DATA_DIR(기본 ~/.teamweaver)에 남는다.
set -euo pipefail
cd "$(dirname "$0")/.."

# .env는 셸 문법으로 읽는다(KEY=값, 공백·$가 든 값은 따옴표로). .env에 같은 키가 있으면 명령줄 값보다 우선한다.
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"

if [[ "${SKIP_SETUP:-0}" != "1" ]]; then
  uv sync
  (cd web && npm ci && npm run build)
  uv run playwright install chromium
fi

if [[ "$HOST" != "127.0.0.1" && "$HOST" != "localhost" \
      && -z "${TEAMWEAVER_ADMIN_PASSWORD_HASH:-}${TEAMWEAVER_ADMIN_PASSWORD:-}${TEAMWEAVER_ADMIN_TOKEN:-}" ]]; then
  echo "경고: $HOST 로 열지만 관리자 비밀번호가 없다 -- 설정 변경·데이터 업로드를 누구나 할 수 있다." >&2
fi

echo "TeamWeaver: http://$HOST:$PORT"
# 시연 기본 데이터: 실제 시스템 형식의 조직형 100명(demo/org-n100). 예전 고정 데이터로 뜨려면 TEAMWEAVER_DEMO_BUNDLE= 로 비운다.
export TEAMWEAVER_DEMO_BUNDLE="${TEAMWEAVER_DEMO_BUNDLE-$PWD/demo/org-n100}"
exec uv run uvicorn api.main:app --host "$HOST" --port "$PORT"
