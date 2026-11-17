"""Create a bounded, source-only workspace for the local SonarScanner.

The repository itself must never be mounted into the scanner. This module uses
an allowlist, refuses symbolic links and copies only code/configuration files
into an empty destination. It never prints file contents or reads environment
files. The caller owns the snapshot directory and its retention policy.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePosixPath
import stat
from typing import NamedTuple


SOURCE_ROOTS = (
    "backend/app",
    "backend/alembic",
    "backend/scripts",
    "backend/tests",
    "frontend/src",
    "frontend/scripts",
    "deploy",
    "scripts",
)
EXPLICIT_FILES = (
    "backend/Dockerfile",
    "frontend/Dockerfile",
    "docker-compose.yml",
    "docker-compose.production.yml",
    "frontend/package.json",
    "frontend/vite.config.js",
    "backend/pyproject.toml",
    "backend/alembic.ini",
)
SOURCE_EXTENSIONS = frozenset(
    {".py", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".css",
     ".scss", ".html", ".sh", ".yml", ".yaml"}
)
EXCLUDED_DIRECTORIES = frozenset(
    {"node_modules", "dist", "build", "coverage", "htmlcov", "test-results",
     "playwright-report", "__pycache__", "venv", "env", "cache", "caches",
     "samples", "sample", "docs", "doc", "mockup", "mockups", "handoff",
     "handoffs", "logs", "log", "reports", "generated", "artifacts"}
)
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_TOTAL_BYTES = 100 * 1024 * 1024
MAX_FILES = 20_000
PROPERTIES_FILE = "sonar-project.properties"
MANIFEST_VERSION = "sonarqube-source-snapshot-v1"


class SnapshotError(ValueError):
    """A safe error code, without file contents or operating-system details."""


class _SourceFile(NamedTuple):
    path: str
    size: int
    device: int
    inode: int
    modified_ns: int


def _absolute_path(path: Path) -> Path:
    path = Path(path)
    if ".." in path.parts:
        raise SnapshotError("parent_path_component_not_allowed")
    return Path(os.path.abspath(path))


def _open_directory(path: Path, *, create: bool = False) -> int:
    """Open each path component independently, never following a symlink."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    descriptor = os.open("/", flags)
    try:
        for component in path.parts[1:]:
            created = False
            if create:
                try:
                    os.mkdir(component, mode=0o755, dir_fd=descriptor)
                    created = True
                except FileExistsError:
                    pass
            next_descriptor = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
            if created:
                os.fchmod(descriptor, 0o755)
        return descriptor
    except OSError:
        os.close(descriptor)
        raise SnapshotError("unsafe_or_unavailable_directory") from None


def _relative_directory(root_descriptor: int, parts: tuple[str, ...], *, create: bool = False) -> int:
    descriptor = os.dup(root_descriptor)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    try:
        for component in parts:
            created = False
            if create:
                try:
                    os.mkdir(component, mode=0o755, dir_fd=descriptor)
                    created = True
                except FileExistsError:
                    pass
            next_descriptor = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
            if created:
                os.fchmod(descriptor, 0o755)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _validate_relative_path(relative: str) -> tuple[str, ...]:
    path = PurePosixPath(relative)
    if path.is_absolute() or not path.parts or any(part in {".", ".."} for part in path.parts):
        raise SnapshotError("unsafe_source_path")
    if any(character in relative for character in ("\\", ",", "\n", "\r", "\t")):
        # These delimiters cannot safely appear in a properties path list.
        raise SnapshotError("unsupported_source_path")
    return path.parts


def _excluded(relative: str, *, directory: bool) -> bool:
    path = PurePosixPath(relative)
    if path.parts[:3] == ("backend", "app", "data"):
        return True
    for part in path.parts:
        lower = part.casefold()
        if lower.startswith(".") or lower.endswith((".egg-info", ".dist-info")):
            return True
        if lower in EXCLUDED_DIRECTORIES or "handoff" in lower:
            return True
    if not directory:
        name = path.name.casefold()
        stem = name.split(".", 1)[0]
        if stem in {"secrets", "credentials", "private_key", "private-key", "service-account"}:
            return True
        if name.startswith(("id_rsa", "id_dsa", "id_ecdsa", "id_ed25519")):
            return True
        if any(marker in name for marker in (".secret.", ".credentials.", ".private-key.")):
            return True
    return False


def _kind(relative: str) -> str:
    path = PurePosixPath(relative)
    if path.parts[:2] == ("backend", "tests"):
        return "test"
    if path.parts[:2] == ("frontend", "src") and path.name.endswith(".test.mjs"):
        return "test"
    if path.parts[0] in {"deploy", "scripts"} and (
        (path.name.startswith("test_") and path.suffix == ".py") or path.name.endswith(".test.mjs")
    ):
        return "test"
    return "source"


def _collect_sources(root_descriptor: int) -> list[_SourceFile]:
    sources: list[_SourceFile] = []
    total_bytes = 0

    def collect_file(relative: str, metadata: os.stat_result) -> None:
        nonlocal total_bytes
        _validate_relative_path(relative)
        if metadata.st_size > MAX_FILE_BYTES:
            raise SnapshotError("source_file_size_limit")
        total_bytes += metadata.st_size
        if total_bytes > MAX_TOTAL_BYTES:
            raise SnapshotError("snapshot_size_limit")
        if len(sources) >= MAX_FILES:
            raise SnapshotError("snapshot_file_count_limit")
        sources.append(_SourceFile(relative, metadata.st_size, metadata.st_dev, metadata.st_ino, metadata.st_mtime_ns))

    def walk(directory_descriptor: int, relative_directory: str) -> None:
        with os.scandir(directory_descriptor) as iterator:
            entries = sorted(iterator, key=lambda entry: entry.name)
        for entry in entries:
            relative = f"{relative_directory}/{entry.name}"
            metadata = entry.stat(follow_symlinks=False)
            if stat.S_ISLNK(metadata.st_mode):
                raise SnapshotError("source_symlink_not_allowed")
            is_directory = stat.S_ISDIR(metadata.st_mode)
            if _excluded(relative, directory=is_directory):
                continue
            if is_directory:
                _validate_relative_path(relative)
                child_descriptor = _relative_directory(directory_descriptor, (entry.name,))
                try:
                    walk(child_descriptor, relative)
                finally:
                    os.close(child_descriptor)
            elif not stat.S_ISREG(metadata.st_mode):
                raise SnapshotError("source_special_file_not_allowed")
            elif PurePosixPath(relative).suffix.casefold() in SOURCE_EXTENSIONS:
                collect_file(relative, metadata)

    for relative in SOURCE_ROOTS:
        try:
            descriptor = _relative_directory(root_descriptor, _validate_relative_path(relative))
        except FileNotFoundError:
            continue
        except OSError:
            raise SnapshotError("source_symlink_or_invalid_directory") from None
        try:
            walk(descriptor, relative)
        finally:
            os.close(descriptor)
    for relative in EXPLICIT_FILES:
        parts = _validate_relative_path(relative)
        try:
            parent_descriptor = _relative_directory(root_descriptor, parts[:-1])
        except FileNotFoundError:
            continue
        except OSError:
            raise SnapshotError("source_symlink_or_invalid_directory") from None
        try:
            try:
                metadata = os.stat(parts[-1], dir_fd=parent_descriptor, follow_symlinks=False)
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(metadata.st_mode):
                raise SnapshotError("source_symlink_not_allowed")
            if not stat.S_ISREG(metadata.st_mode):
                raise SnapshotError("source_special_file_not_allowed")
            collect_file(relative, metadata)
        finally:
            os.close(parent_descriptor)
    return sorted(sources, key=lambda item: item.path)


def _read_source(root_descriptor: int, source: _SourceFile) -> bytes:
    parts = _validate_relative_path(source.path)
    parent_descriptor = _relative_directory(root_descriptor, parts[:-1])
    try:
        descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=parent_descriptor)
    finally:
        os.close(parent_descriptor)
    try:
        metadata = os.fstat(descriptor)
        expected = (source.device, source.inode, source.size, source.modified_ns)
        actual = (metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns)
        if not stat.S_ISREG(metadata.st_mode) or actual != expected:
            raise SnapshotError("source_changed_during_snapshot")
        with os.fdopen(os.dup(descriptor), "rb") as stream:
            content = stream.read(MAX_FILE_BYTES + 1)
        after = os.fstat(descriptor)
        if len(content) != source.size or len(content) > MAX_FILE_BYTES or after.st_mtime_ns != source.modified_ns:
            raise SnapshotError("source_changed_during_snapshot")
        if b"\x00" in content:
            raise SnapshotError("non_text_source_file")
        return content
    finally:
        os.close(descriptor)


def _write_new(root_descriptor: int, relative: str, content: bytes) -> None:
    parts = _validate_relative_path(relative)
    parent_descriptor = _relative_directory(root_descriptor, parts[:-1], create=True)
    try:
        descriptor = os.open(parts[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o644, dir_fd=parent_descriptor)
    finally:
        os.close(parent_descriptor)
    with os.fdopen(descriptor, "wb") as stream:
        os.fchmod(stream.fileno(), 0o644)
        stream.write(content)


def _properties(files: list[dict]) -> bytes:
    sources = [item["path"] for item in files if item["kind"] == "source"]
    tests = [item["path"] for item in files if item["kind"] == "test"]
    lines = [
        "sonar.projectKey=waf-ai-console-local",
        "sonar.projectName=WAF AI Console (local)",
        "sonar.sourceEncoding=UTF-8",
        "sonar.python.version=3.12",
        "sonar.scm.disabled=true",
        "sonar.sources=" + ",".join(sources),
    ]
    if tests:
        lines.append("sonar.tests=" + ",".join(tests))
    return ("\n".join(lines) + "\n").encode("utf-8")


def build_snapshot(repo_root: Path, destination: Path) -> dict:
    """Copy approved sources and return hashes/counts, never source contents.

    Destination must be a real empty directory (or not exist). It cannot be the
    repository, an ancestor of it, or nested under any source root. A failure
    during copying may leave a partial destination; it is never reused or
    overwritten by a later call. Its immediate parent must already exist, be
    owned by the current user and have exactly 0700 permissions. Reader access
    inside that private boundary remains 0755/0644 for the non-root scanner.
    Symbolic links and parent traversal are refused.
    Coverage is deliberately not configured because no coverage was measured.
    """
    repository = _absolute_path(repo_root)
    target = _absolute_path(destination)
    if target == repository or repository.is_relative_to(target):
        raise SnapshotError("destination_overlaps_repository")
    if any(target.is_relative_to(repository / relative) for relative in SOURCE_ROOTS):
        raise SnapshotError("destination_overlaps_source_root")
    root_descriptor = _open_directory(repository)
    destination_descriptor = None
    parent_descriptor = None
    try:
        # A non-root scanner needs readable snapshot files, so protect their
        # existing outer boundary instead of exposing a standalone 0755 tree.
        # Refuse before enumerating or reading source files; never chmod a
        # caller-owned existing parent as an automatic repair.
        parent_descriptor = _open_directory(target.parent)
        metadata = os.fstat(parent_descriptor)
        if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) != 0o700:
            raise SnapshotError("destination_parent_requires_owned_0700_directory")
        # Validate a pre-existing destination before touching the source tree.
        try:
            destination_descriptor = _relative_directory(parent_descriptor, (target.name,))
        except FileNotFoundError:
            pass
        except OSError:
            raise SnapshotError("unsafe_or_unavailable_directory") from None
        if destination_descriptor is not None and os.listdir(destination_descriptor):
            raise SnapshotError("destination_not_empty")
        sources = _collect_sources(root_descriptor)
        if not sources or not any(_kind(item.path) == "source" for item in sources):
            raise SnapshotError("no_source_files")
        if destination_descriptor is None:
            # Stay anchored to the already verified private inode even if an
            # ancestor is renamed between validation and destination creation.
            destination_descriptor = _relative_directory(parent_descriptor, (target.name,), create=True)
        if os.listdir(destination_descriptor):
            raise SnapshotError("destination_not_empty")
        os.fchmod(destination_descriptor, 0o755)
        files = []
        for source in sources:
            content = _read_source(root_descriptor, source)
            _write_new(destination_descriptor, source.path, content)
            files.append({"path": source.path, "sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content), "kind": _kind(source.path)})
        _write_new(destination_descriptor, PROPERTIES_FILE, _properties(files))
        return {
            "schema_version": MANIFEST_VERSION,
            "files": files,
            "file_count": len(files),
            "source_file_count": sum(item["kind"] == "source" for item in files),
            "test_file_count": sum(item["kind"] == "test" for item in files),
            "total_bytes": sum(item["bytes"] for item in files),
            "properties_file": PROPERTIES_FILE,
        }
    except SnapshotError:
        raise
    except OSError:
        raise SnapshotError("snapshot_filesystem_error") from None
    finally:
        if destination_descriptor is not None:
            os.close(destination_descriptor)
        if parent_descriptor is not None:
            os.close(parent_descriptor)
        os.close(root_descriptor)
