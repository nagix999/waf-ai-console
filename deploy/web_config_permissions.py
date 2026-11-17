"""Give only the non-root Nginx group access to mounted WAF proxy config.

This maintenance tool changes metadata, never config contents or operator UID.
Its fixed CLI mount paths cannot target environment files or the database.
"""
import os
from pathlib import Path
import stat
import sys

NGINX_GID = 101
POLICY_DIRECTORY = Path("/policy")
SITE_CONFIG = Path("/site.conf")
POLICY_FILES = frozenset({"http.conf", "server.conf", "proxy.conf"})
MANIFEST = "manifest.json"


def _regular_file(path, *, directory=None):
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=directory)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("single_link_regular_config_required")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def prepare_config(policy, site=None):
    """Validate every target before granting group read or directory traversal."""
    directory = os.open(policy, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    opened = []
    targets = []
    try:
        names = set(os.listdir(directory))
        if not POLICY_FILES <= names or not names <= POLICY_FILES | {MANIFEST}:
            raise ValueError("proxy_policy_entries_invalid")
        for name in sorted(names):
            descriptor = _regular_file(name, directory=directory)
            opened.append(descriptor)
            if name == MANIFEST:
                if stat.S_IMODE(os.fstat(descriptor).st_mode) != 0o600:
                    raise ValueError("private_manifest_permissions_required")
            else:
                targets.append(descriptor)
        if site is not None:
            descriptor = _regular_file(site)
            opened.append(descriptor)
            targets.append(descriptor)
        for descriptor in targets:
            os.fchown(descriptor, -1, NGINX_GID)
            os.fchmod(descriptor, 0o640)
        os.fchown(directory, -1, NGINX_GID)
        os.fchmod(directory, 0o750)
    finally:
        for descriptor in opened:
            os.close(descriptor)
        os.close(directory)


def main(argv=None):
    arguments = sys.argv[1:] if argv is None else argv
    if arguments != ["--prepare"] or os.geteuid() != 0 or not os.path.ismount(POLICY_DIRECTORY):
        print("Requires root maintenance user, --prepare and a mounted /policy directory.", file=sys.stderr)
        return 1
    try:
        # lexists also detects a dangling symlink instead of silently skipping it.
        site = SITE_CONFIG if os.path.lexists(SITE_CONFIG) else None
        if site is not None and not os.path.ismount(site):
            raise ValueError("site_config_mount_required")
        prepare_config(POLICY_DIRECTORY, site)
    except (OSError, ValueError):
        print("Proxy config permission preparation failed; no file contents were printed. Check mounts, entries and ownership before starting Nginx.", file=sys.stderr)
        return 1
    print("Prepared proxy config for Nginx group 101; operator ownership, contents and private manifest unchanged.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
