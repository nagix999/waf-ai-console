"""Small, fail-closed primitives. Subprocess output is private by default."""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile


class DeployError(Exception):
    """Only constant, operator-safe messages belong in this exception."""


class Runner:
    def __init__(self):
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith(("WAF_", "COMPOSE_", "PLATFORM_", "PRODUCTION_", "BUILDX_", "BUILDKIT_"))
                    and k not in {"BASH_ENV", "ENV", "PYTHONPATH", "PYTHONHOME", "GH_DEBUG", "EXPERIMENTAL_BUILDKIT_SOURCE_POLICY"}}
        # All Docker operations must use the same explicit, local context.
        self.env["DOCKER_BUILDKIT"] = "1"
        self.env["BUILDX_BUILDER"] = "default"

    def run(self, args, *, code="command_failed", input=None, timeout=60, env=None):
        try:
            result = subprocess.run([str(arg) for arg in args], input=input, text=True,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    timeout=timeout, env=self.env if env is None else env)
        except (OSError, subprocess.TimeoutExpired):
            raise DeployError(code) from None
        if result.returncode:
            raise DeployError(code)
        return result.stdout


def json_output(runner, args, *, code="invalid_command_result", **kwargs):
    try:
        return json.loads(runner.run(args, code=code, **kwargs))
    except (ValueError, TypeError):
        raise DeployError(code) from None


def digest(value):
    if isinstance(value, str):
        value = value.encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compose_model(value):
    """Decode a Compose config JSON document into actual runtime literals.

Compose config serializes dollar signs as $$ even in --format json output so
that the result can be fed back to Compose. Decode exactly once on ingestion.
Do not call this on Docker inspect output or an already-decoded model.
"""
    if isinstance(value, str):
        return value.replace("$$", "$")
    if isinstance(value, list):
        return [compose_model(item) for item in value]
    if isinstance(value, dict):
        return {key: compose_model(item) for key, item in value.items()}
    return value


def compose_text(model):
    """Serialize a raw model with one Compose dollar-escaping layer."""
    def escape(value):
        if isinstance(value, str):
            return value.replace("$", "$$")
        if isinstance(value, list):
            return [escape(item) for item in value]
        if isinstance(value, dict):
            return {key: escape(item) for key, item in value.items()}
        return value
    return canonical(escape(model)) + "\n"


def regular_file(path, *, private=False):
    path = Path(path)
    try:
        mode = path.lstat().st_mode
        if not stat.S_ISREG(mode) or (private and mode & 0o077):
            raise DeployError("unsafe_file_permissions_or_symlink")
        return path
    except OSError:
        raise DeployError("required_file_unavailable") from None


def secure_dir(path):
    path = Path(path)
    # Reject symlink ancestors, including those above an existing directory.
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise DeployError("symlink_directory_not_allowed")
    try:
        if not path.parent.exists():
            secure_dir(path.parent)
        path.mkdir(mode=0o700, exist_ok=True)
        if not path.is_dir() or path.stat().st_mode & 0o077:
            raise DeployError("private_directory_required")
    except OSError:
        raise DeployError("private_directory_unavailable") from None
    return path


def private_write(path, value):
    path = Path(path)
    secure_dir(path.parent)
    if path.is_symlink():
        raise DeployError("symlink_file_not_allowed")
    if not isinstance(value, str):
        value = canonical(value) + "\n"
    temporary = None
    try:
        fd, temporary = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        raise DeployError("private_state_write_failed") from None
    finally:
        if temporary:
            os.unlink(temporary)


def private_json(path):
    try:
        return json.loads(regular_file(path, private=True).read_text(encoding="utf-8"))
    except (ValueError, OSError, UnicodeError):
        raise DeployError("private_state_invalid") from None


@contextmanager
def operator_lock(path, *, create=True):
    path = Path(path)
    if create:
        secure_dir(path.parent)
    fd = None
    try:
        fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW | (os.O_CREAT if create else 0), 0o600)
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise DeployError("invalid_operator_lock")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, BlockingIOError):
        if fd is not None:
            os.close(fd)
        raise DeployError("operator_lock_busy_or_unavailable") from None
    except BaseException:
        if fd is not None:
            os.close(fd)
        raise
    try:
        yield
    finally:
        if fd is not None:
            os.close(fd)
