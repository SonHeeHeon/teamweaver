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

# 앱 전체 로그인(2026-10-18): 비밀번호를 정하지 않으면 기본 계정 admin / admin1234로 로그인한다(시연용).
# 다른 사람이 접속하는 주소(HOST=0.0.0.0 등)로 기본 계정을 열지 않는다 -- 비밀번호를 정해야 뜬다(폴백 리뷰 S5).
if [[ "$HOST" != "127.0.0.1" && "$HOST" != "localhost" && "${TEAMWEAVER_LOGIN:-}" != "off" \
      && -z "${TEAMWEAVER_ADMIN_PASSWORD_HASH:-}${TEAMWEAVER_ADMIN_PASSWORD:-}${TEAMWEAVER_ADMIN_TOKEN:-}" ]]; then
  echo "중단: $HOST 로 열려면 기본 계정(admin/admin1234) 대신 비밀번호를 정한다:" >&2
  echo '  TEAMWEAVER_ADMIN_PASSWORD_HASH="$(uv run python scripts/hash_admin_password.py)" scripts/run_poc.sh' >&2
  exit 1
fi
echo "로그인: 아이디 ${TEAMWEAVER_ADMIN_ID:-admin} · 비밀번호는 TEAMWEAVER_ADMIN_PASSWORD(_HASH), 정하지 않았으면 admin1234"

echo "TeamWeaver: http://$HOST:$PORT"
# 시작 데이터(기본): 연말 시점 100명(demo/org-n100-yearend -- 진행 사업·끝나 가는 사업·발주 예정 사업이 섞여 있어
# 신규 사업 편성·긴급 투입·연간 계획 세 상황을 한 데이터로 보인다, claude-b 요청 3 2026-10-18). 그 묶음이 없으면
# 연초 계획 100명(demo/org-n100). 예전 고정 데이터로 뜨려면 TEAMWEAVER_DEMO_BUNDLE= 로 비운다.
# 시드 4개 동시 풀이로 100명 안 A~D가 약 2분(30초 x 4)에 빈자리 없이 나온다(rehearsal/results/n100/pipeline-real.json).
if [ -d "$PWD/demo/org-n100-yearend" ]; then _default_bundle="$PWD/demo/org-n100-yearend"; else _default_bundle="$PWD/demo/org-n100"; fi
export TEAMWEAVER_DEMO_BUNDLE="${TEAMWEAVER_DEMO_BUNDLE-$_default_bundle}"
# 시연 편성안은 화면의 "편성 기록"에서 demo/records/*.json을 열어 본다(2026-10-18, 예전 시연 고르기·미리 계산 자동 설치를 대체).
export TEAMWEAVER_DEMO_DIR="${TEAMWEAVER_DEMO_DIR-$PWD/demo}"
# 동시 탐색 수(이 기기 CPU 코어에 맞춘 값). 1이면 안 B 이후가 품질 하한에 걸려 안 A 하나만 나올 수 있다(실측).
export TEAMWEAVER_SOLVER_SEEDS="${TEAMWEAVER_SOLVER_SEEDS-4}"
# 시연 데이터는 시간 한도 안에서 최선 증명까지 가지 않아 결과를 캐시하지 않는다 -- 부팅 사전계산은 기동만 ~2분 늦추고
# 얻는 것이 없으니 끈다(예전 고정 데이터로 뜰 때는 그대로 사전계산).
if [[ -n "$TEAMWEAVER_DEMO_BUNDLE" ]]; then
  export TEAMWEAVER_SKIP_WARM="${TEAMWEAVER_SKIP_WARM-1}"
fi
exec uv run uvicorn api.main:app --host "$HOST" --port "$PORT"
