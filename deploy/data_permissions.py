"""Prepare only the stopped WAF SQLite volume for its non-root runtime.

No database contents, secrets, recursive traversal, or arbitrary CLI paths.
The caller must stop every volume consumer and verify its backup first.
"""
import os
from pathlib import Path
import stat
import sys

RUNTIME_UID = 10001
RUNTIME_GID = 10001
DATA_DIRECTORY = Path("/data")
DATABASE_FILES = frozenset({"waf.db", "waf.db-wal", "waf.db-shm", "waf.db-journal"})


def prepare_directory(path, uid=RUNTIME_UID, gid=RUNTIME_GID):
    """Prepare SQLite files only, leaving unrelated volume entries untouched.

    Existing deployments can keep backups beside the database. Enumerating the
    directory must not turn those entries into maintenance targets: only the
    exact SQLite names are opened, and every opened target is validated before
    any ownership or mode changes. A nonempty volume must contain the main
    database so that an unrelated directory is not mistaken for the WAF volume.
    """
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    files = []
    try:
        names = os.listdir(descriptor)
        if names and "waf.db" not in names:
            raise ValueError("data_volume_database_required")
        for name in sorted(DATABASE_FILES.intersection(names)):
            item = os.open(name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=descriptor)
            files.append(item)
            info = os.fstat(item)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError("data_volume_regular_unlinked_files_required")
        # No shared/writable directory or world-readable database after handoff.
        for item in files:
            os.fchown(item, uid, gid)
            os.fchmod(item, 0o600)
        os.fchown(descriptor, uid, gid)
        os.fchmod(descriptor, 0o700)
    finally:
        for item in files:
            os.close(item)
        os.close(descriptor)


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if args != ["--stopped-and-backed-up"] or os.geteuid() != 0 or not os.path.ismount(DATA_DIRECTORY):
        print("Requires a mounted /data volume, root maintenance user, stopped consumers and verified backup.", file=sys.stderr)
        return 1
    try:
        prepare_directory(DATA_DIRECTORY)
    except (OSError, ValueError):
        print("Data permission preparation failed; no database values were printed. Check volume entries and ownership before restarting.", file=sys.stderr)
        return 1
    print("Prepared WAF SQLite volume for UID/GID 10001; database contents unchanged.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
