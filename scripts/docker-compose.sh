#!/usr/bin/env bash
# Repair existing SQLite permissions after building the new images.
# No Git operations, image builds/downloads, volume deletion, or DB rollback.
# Host Python is not required; checks use Python already in the API image.
set -euo pipefail
umask 077

WAF_ROOT="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$WAF_ROOT"
WAF_PROJECT="${WAF_PROJECT:-waf-standalone}"
WAF_ENV_FILE="${WAF_ENV_FILE:-.env}"
WAF_COMPOSE_FILE="${WAF_COMPOSE_FILE:-docker-compose.yml}"
WAF_RECOVERY_DIR=''
WAF_PHASE='사전 확인'
WAF_LOCAL_IMAGES_FILE=''

fail() { printf '중단: %s\n' "$1" >&2; exit 1; }
on_exit() {
  local result=$?
  if [ "$result" -ne 0 ]; then
    printf '중단된 단계: %s. 이후 단계와 자동 복원은 실행하지 않았습니다.\n' "$WAF_PHASE" >&2
    if [ -n "$WAF_RECOVERY_DIR" ]; then
      printf '복구 작업 폴더: %s (백업 성공 여부는 위 단계에서 확인하세요.)\n' "$WAF_RECOVERY_DIR" >&2
    fi
  fi
}
trap on_exit EXIT

if [ "$#" -ne 0 ]; then
  fail '인자 없이 실행하세요. 설정 변경은 WAF_PROJECT, WAF_ENV_FILE, WAF_COMPOSE_FILE 환경 변수를 사용하세요.'
fi
for tool in docker flock mktemp; do
  command -v "$tool" >/dev/null || fail "필요한 명령이 없습니다: $tool"
done
for path in "$WAF_ENV_FILE" "$WAF_COMPOSE_FILE" deploy/redeploy_db.py deploy/compose_recovery_checks.py; do
  [ -f "$path" ] || fail "필요한 파일이 없습니다: $path"
done

mkdir -p "$WAF_ROOT/.local-deploy/redeploy"
exec 9>"$WAF_ROOT/.local-deploy/redeploy/operation.lock"
flock -n 9 || fail '다른 배포 작업이 실행 중입니다.'

waf() {
  local options=(--env-file "$WAF_ENV_FILE" -p "$WAF_PROJECT" -f "$WAF_COMPOSE_FILE")
  if [ -n "$WAF_LOCAL_IMAGES_FILE" ]; then
    options+=(-f "$WAF_LOCAL_IMAGES_FILE")
  fi
  docker compose "${options[@]}" "$@"
}
waf config --quiet
WAF_API_CONTAINER="$(waf ps -aq api)"
[[ -n "$WAF_API_CONTAINER" && "$WAF_API_CONTAINER" != *$'\n'* ]] || \
  fail '기존 API 컨테이너 하나가 필요합니다. 프로젝트명과 Compose 파일을 확인하세요.'
WAF_DATA_VOLUME="$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/data"}}{{if eq .Type "volume"}}{{if .RW}}{{.Name}}{{end}}{{end}}{{end}}{{end}}' "$WAF_API_CONTAINER")"
[ -n "$WAF_DATA_VOLUME" ] || fail '기존 API의 쓰기 가능한 /data 데이터 볼륨을 찾지 못했습니다.'

# Bootstrap checks need only the existing local image, not a running API or DB.
WAF_CHECK_IMAGE="$(docker inspect --format '{{.Image}}' "$WAF_API_CONTAINER")"
[[ "$WAF_CHECK_IMAGE" =~ ^sha256:[0-9a-f]{64}$ ]] || fail '기존 API의 이미지 ID를 확인하지 못했습니다.'
docker image inspect "$WAF_CHECK_IMAGE" >/dev/null 2>&1 || \
  fail '설정 검사에 사용할 기존 API 이미지가 로컬에 없습니다.'
WAF_DOCKER_RUN_HELP="$(docker run --help)"
[[ "$WAF_DOCKER_RUN_HELP" == *--pull* ]] || \
  fail '현재 Docker는 검사 컨테이너의 이미지 다운로드 차단 옵션을 지원하지 않습니다.'

# Parse privately in an isolated container. Only the no-pull policy reaches disk.
# --pull=never belongs to raw Docker, not the incompatible Compose run option.
WAF_OVERRIDE_CANDIDATE="$(mktemp "$WAF_ROOT/.local-deploy/compose-local-images.XXXXXXXX.json")"
waf config --format json | docker run --rm --pull=never -i --network none \
  --read-only --cap-drop ALL --cap-add DAC_OVERRIDE --security-opt no-new-privileges --user 0:0 \
  --mount "type=bind,src=$WAF_ROOT/deploy/compose_recovery_checks.py,dst=/opt/waf-compose-checks.py,readonly" \
  --entrypoint python "$WAF_CHECK_IMAGE" \
  /opt/waf-compose-checks.py config "$WAF_DATA_VOLUME" > "$WAF_OVERRIDE_CANDIDATE"
WAF_LOCAL_IMAGES_FILE="$WAF_OVERRIDE_CANDIDATE"

# Enforce offline operation without relying on version-specific --pull flags.
# Validate support and local image availability before stopping any services.
waf config --quiet
WAF_LOCAL_IMAGES="$(waf config --images)"
[ -n "$WAF_LOCAL_IMAGES" ] || fail '사용할 로컬 이미지를 확인하지 못했습니다.'
while IFS= read -r image; do
  [ -n "$image" ] || continue
  docker image inspect "$image" >/dev/null 2>&1 || \
    fail '필요한 로컬 이미지가 없습니다. 먼저 같은 설정으로 빌드하거나 이미지를 반입하세요.'
done <<< "$WAF_LOCAL_IMAGES"

WAF_PHASE='서비스 중지'
printf '%s\n' "$WAF_PHASE"
waf stop
[ -z "$(docker ps -q --filter "volume=$WAF_DATA_VOLUME")" ] || \
  fail 'DB 볼륨을 사용하는 컨테이너가 아직 실행 중입니다.'

WAF_PHASE='DB 마운트 확인'
waf run --rm --no-deps -T --user 0:0 --entrypoint python \
  -v "$WAF_ROOT/deploy/compose_recovery_checks.py:/opt/waf-compose-checks.py:ro" \
  api /opt/waf-compose-checks.py mount

WAF_RECOVERY_DIR="$(mktemp -d "$WAF_ROOT/.local-deploy/db-recovery.XXXXXXXX")"
dbtool() {
  waf run --rm --no-deps -T --user 0:0 --entrypoint python \
    -v "$WAF_ROOT/deploy/redeploy_db.py:/opt/waf-db-check.py:ro" \
    -v "$WAF_RECOVERY_DIR:/backup" \
    api /opt/waf-db-check.py "$@"
}

WAF_PHASE='DB 백업과 무결성 확인'
printf '%s\n' "$WAF_PHASE"
dbtool backup /data/waf.db /backup/waf.db /backup/baseline.json "$(id -u)" "$(id -g)"
dbtool verify /data/waf.db /backup/baseline.json
printf '검증된 DB 백업: %s/waf.db\n' "$WAF_RECOVERY_DIR"

WAF_PHASE='현재 DB 파일 권한 준비'
waf run --rm --no-deps -T --user 0:0 --entrypoint python \
  api /opt/waf-data-permissions.py --stopped-and-backed-up

WAF_PHASE='DB 마이그레이션'
waf run --rm --no-deps -T --user 10001:10001 api alembic upgrade head
WAF_PHASE='기존 데이터 보존 확인'
dbtool verify /data/waf.db /backup/baseline.json
waf run --rm --no-deps -T --user 10001:10001 api alembic current

WAF_PHASE='서비스 재기동과 상태 확인'
waf up -d --no-build --wait
waf ps
printf '완료. DB 백업: %s/waf.db\n' "$WAF_RECOVERY_DIR"
