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
# 시연 데이터(기본): 실제 시스템 형식의 조직형 100명(demo/org-n100). 예전 고정 데이터로 뜨려면 TEAMWEAVER_DEMO_BUNDLE= 로 비운다.
# 계산 안정화 뒤 전환(2026-10-06): 익숙한 쌍 = 최근 3년 중 12개월(사용자 결정), 시드 4개 동시 풀이로 100명 안 A~D가
# 약 2분(30초 x 4)에 빈자리 없이 나온다(rehearsal/results/n100/pipeline-real.json).
export TEAMWEAVER_DEMO_BUNDLE="${TEAMWEAVER_DEMO_BUNDLE-$PWD/demo/org-n100}"
# 시연 묶음 목록(2026-10-06, 데이터 탭에서 고름): 연초 계획(전원 배치)과 운영 중(대기 인력으로 신규 제안 편성), 100/200/300명.
# demo/precomputed/에 같은 데이터·같은 설정으로 미리 계산한 결과가 있으면 "미리 계산"으로 바로 보인다(python -m rehearsal.precompute_demo).
export TEAMWEAVER_DEMO_DIR="${TEAMWEAVER_DEMO_DIR-$PWD/demo}"
# 동시 탐색 수(이 기기 CPU 코어에 맞춘 값). 1이면 안 B 이후가 품질 하한에 걸려 안 A 하나만 나올 수 있다(실측).
export TEAMWEAVER_SOLVER_SEEDS="${TEAMWEAVER_SOLVER_SEEDS-4}"
# 시연 데이터는 시간 한도 안에서 최선 증명까지 가지 않아 결과를 캐시하지 않는다 -- 부팅 사전계산은 기동만 ~2분 늦추고
# 얻는 것이 없으니 끈다(예전 고정 데이터로 뜰 때는 그대로 사전계산).
if [[ -n "$TEAMWEAVER_DEMO_BUNDLE" ]]; then
  export TEAMWEAVER_SKIP_WARM="${TEAMWEAVER_SKIP_WARM-1}"
fi
exec uv run uvicorn api.main:app --host "$HOST" --port "$PORT"
