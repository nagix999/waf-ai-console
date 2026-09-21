#!/bin/sh
# Commit reviewed source changes and push explicit refs only. Never force push.
set -eu
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
exec python3 -I -B "$script_dir/../deploy/github_upload.py" "$@"
