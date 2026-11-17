"""Read-only recovery preflight checks, run inside the existing API image.

Resolved Compose configuration may contain secrets. Never echo that input or
exception details. The config command emits only service names and an offline
image policy; the mount command does not open database contents or change files.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import TextIO


DATABASE_URL = "sqlite+pysqlite:////data/waf.db"
DATABASE_FILES = ("waf.db", "waf.db-wal", "waf.db-shm", "waf.db-journal")
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
CONFIG_ERROR = "중단: Compose의 DB 설정이 기존 데이터 볼륨과 다르거나 지원 범위 밖입니다."
MOUNT_ERROR = "DB 파일 또는 마운트 확인 실패. 권한 변경은 실행하지 않았습니다."
USAGE_ERROR = "중단: 복구 검사 명령과 인자를 확인하세요."


def _invalid_config() -> None:
    raise ValueError("invalid_compose_configuration")


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            _invalid_config()
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    _invalid_config()


def offline_override(config: object, expected_volume: str) -> dict:
    """Validate the existing SQLite volume and return a secret-free override."""
    if not isinstance(expected_volume, str) or not IDENTIFIER.fullmatch(expected_volume):
        _invalid_config()
    if not isinstance(config, dict):
        _invalid_config()
    services = config.get("services")
    if not isinstance(services, dict) or not services:
        _invalid_config()
    for name, definition in services.items():
        if (not isinstance(name, str) or not IDENTIFIER.fullmatch(name)
                or not isinstance(definition, dict)):
            _invalid_config()
    api = services.get("api")
    if not isinstance(api, dict):
        _invalid_config()
    mounts = api.get("volumes")
    if not isinstance(mounts, list) or any(not isinstance(item, dict) for item in mounts):
        _invalid_config()
    data_mounts = [item for item in mounts if item.get("target") == "/data"]
    if len(data_mounts) != 1:
        _invalid_config()
    mount = data_mounts[0]
    if mount.get("type") != "volume" or mount.get("read_only", False) is not False:
        _invalid_config()
    source = mount.get("source")
    volumes = config.get("volumes")
    if not isinstance(source, str) or not isinstance(volumes, dict):
        _invalid_config()
    volume = volumes.get(source)
    if not isinstance(volume, dict) or volume.get("name") != expected_volume:
        _invalid_config()
    environment = api.get("environment")
    if not isinstance(environment, dict) or environment.get("WAF_DATABASE_URL") != DATABASE_URL:
        _invalid_config()
    return {"services": {name: {"pull_policy": "never"} for name in services}}


def validate_mount(directory: Path = Path("/data")) -> None:
    """Check only current DB inode metadata on a writable mounted directory."""
    directory = Path(directory)
    if (not stat.S_ISDIR(os.lstat(directory).st_mode)
            or not os.path.ismount(directory)
            or os.statvfs(directory).f_flag & os.ST_RDONLY):
        raise ValueError("invalid_database_mount")
    for name in DATABASE_FILES:
        path = directory / name
        if name != "waf.db" and not os.path.lexists(path):
            continue
        info = os.lstat(path)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("invalid_database_file")


def main(argv: list[str] | None = None, *, stdin: TextIO | None = None,
         stdout: TextIO | None = None, stderr: TextIO | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    source = sys.stdin if stdin is None else stdin
    output = sys.stdout if stdout is None else stdout
    errors = sys.stderr if stderr is None else stderr
    if len(args) == 2 and args[0] == "config":
        try:
            config = json.load(source, object_pairs_hook=_unique_object,
                               parse_constant=_reject_constant)
            override = offline_override(config, args[1])
            json.dump(override, output, separators=(",", ":"))
            output.write("\n")
            return 0
        except Exception:
            print(CONFIG_ERROR, file=errors)
            return 1
    if args == ["mount"]:
        try:
            validate_mount()
            return 0
        except Exception:
            print(MOUNT_ERROR, file=errors)
            return 1
    print(USAGE_ERROR, file=errors)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
