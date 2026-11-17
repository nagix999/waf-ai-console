#!/bin/sh
# Local diagnostics only; never invoke the WAF deployment or source an env file.
set -eu
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
exec python3 -I -B "$script_dir/../deploy/sonarqube/manage.py" "$@"
