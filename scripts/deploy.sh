#!/bin/sh
# Default: review, publish to main, then redeploy this host's existing WAF stack.
# Preserve the old explicit Gateway commands for existing installations.
set -eu
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
case "${1-}" in
  gateway)
    shift
    exec python3 -I -B "$script_dir/../deploy/automation/cli.py" "$@"
    ;;
  check|deploy|status|rollback)
    echo "기존 Gateway 연동 명령입니다. 통합 GitHub·Docker 배포는 인자 없이, 검사는 --check로 실행하세요." >&2
    exec python3 -I -B "$script_dir/../deploy/automation/cli.py" "$@"
    ;;
  *)
    exec python3 -I -B "$script_dir/../deploy/release.py" "$@"
    ;;
esac
