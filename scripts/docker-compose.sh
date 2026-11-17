#!/usr/bin/env bash
# Repair existing SQLite permissions after building the new images.
# No Git operations, image builds/downloads, volume deletion, or DB rollback.
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
for tool in docker python3 flock mktemp; do
  command -v "$tool" >/dev/null || fail "필요한 명령이 없습니다: $tool"
done
for path in "$WAF_ENV_FILE" "$WAF_COMPOSE_FILE" deploy/redeploy_db.py; do
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

# Consume resolved configuration privately; never print its environment values.
WAF_OVERRIDE_CANDIDATE="$(mktemp "$WAF_ROOT/.local-deploy/compose-local-images.XXXXXXXX.json")"
waf config --format json | python3 -c '
import json, sys
try:
    config = json.load(sys.stdin)
    api = config["services"]["api"]
    mounts = [v for v in api["volumes"] if v["target"] == "/data"]
    assert len(mounts) == 1
    mount = mounts[0]
    assert mount["type"] == "volume" and not mount.get("read_only", False)
    assert config["volumes"][mount["source"]]["name"] == sys.argv[1]
    assert api["environment"]["WAF_DATABASE_URL"] == "sqlite+pysqlite:////data/waf.db"
    # Older Compose supports this policy but not the run --pull CLI option.
    # Store only service names and policy, never resolved environment values.
    override = {"services": {name: {"pull_policy": "never"} for name in config["services"]}}
    with open(sys.argv[2], "w") as stream:
        json.dump(override, stream)
except Exception:
    sys.exit("중단: Compose의 DB 설정이 기존 데이터 볼륨과 다르거나 지원 범위 밖입니다.")
' "$WAF_DATA_VOLUME" "$WAF_OVERRIDE_CANDIDATE"
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
waf run --rm --no-deps -T --user 0:0 --entrypoint python api -c '
import os, stat, sys
try:
    assert os.path.ismount("/data")
    assert not (os.statvfs("/data").f_flag & os.ST_RDONLY)
    for name in ("waf.db", "waf.db-wal", "waf.db-shm", "waf.db-journal"):
        path = "/data/" + name
        if name != "waf.db" and not os.path.lexists(path):
            continue
        info = os.lstat(path)
        assert stat.S_ISREG(info.st_mode) and info.st_nlink == 1
except Exception:
    sys.exit("DB 파일 또는 마운트 확인 실패. 권한 변경은 실행하지 않았습니다.")
'

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
