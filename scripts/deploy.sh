#!/bin/sh
# No env sourcing: all operator settings are parsed as literal data by Python.
set -eu
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
exec python3 -I -B "$script_dir/../deploy/automation/cli.py" "$@"
