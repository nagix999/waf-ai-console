#!/bin/sh
# Reuse the existing WAF deployment; never source an env file as shell code.
set -eu
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
exec python3 -I -B "$script_dir/../deploy/redeploy.py" "$@"
