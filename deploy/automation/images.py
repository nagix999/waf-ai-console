"""Source-bound local image preparation; never changes running containers."""
import hashlib
import os
from pathlib import Path
import re
import stat

from .common import DeployError, canonical, digest, json_output, regular_file


OWNER_LABEL = "io.waf.deploy.owner"
SOURCE_LABEL = "io.waf.deploy.source"
GENERATION_LABEL = "io.waf.deploy.generation"
EXCLUDED_DIRECTORIES = {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache",
                        ".ruff_cache", ".cache", ".local-deploy", ".agents", ".codex", "dist", "build",
                        "coverage", "htmlcov", "keys", "secrets"}
IMAGE_ID = re.compile(r"sha256:[a-f0-9]{64}")


def _excluded(name):
    return (name in EXCLUDED_DIRECTORIES or name.startswith((".env", "dist-", "id_rsa", "id_ed25519"))
            or name.endswith((".env", ".key", ".pem", ".p12", ".pfx", ".pyc", ".pyo", ".log", ".db", ".sqlite", ".sqlite3"))
            or re.search(r"\.(?:db|sqlite|sqlite3)-(?:wal|shm|journal)$", name) is not None)


def source_fingerprint(root):
    """Fingerprint source files deterministically, excluding private/runtime data.

Executable permission bits are included because they may affect Docker COPY.
Source symlinks and unusual filesystem nodes fail closed; excluded private or
generated directories are never followed or read.
"""
    root = Path(root).absolute()
    if not root.is_dir() or any(path.is_symlink() for path in (root, *root.parents)):
        raise DeployError("source_root_invalid")
    inventory = []
    try:
        for current, directories, names in os.walk(root, followlinks=False):
            directories[:] = sorted(name for name in directories if not _excluded(name))
            for name in directories:
                if (Path(current) / name).is_symlink():
                    raise DeployError("source_symlink_not_allowed")
            for name in sorted(names):
                if _excluded(name):
                    continue
                path = Path(current) / name
                mode = path.lstat().st_mode
                if not stat.S_ISREG(mode):
                    raise DeployError("source_regular_files_required")
                file_hash = hashlib.sha256()
                fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
                with os.fdopen(fd, "rb") as stream:
                    before = os.fstat(stream.fileno())
                    if not stat.S_ISREG(before.st_mode):
                        raise DeployError("source_regular_files_required")
                    while block := stream.read(1024 * 1024):
                        file_hash.update(block)
                    after = os.fstat(stream.fileno())
                if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
                    raise DeployError("source_changed_during_fingerprint")
                inventory.append((path.relative_to(root).as_posix(), mode & 0o111, file_hash.hexdigest()))
    except OSError:
        raise DeployError("source_fingerprint_unavailable") from None
    if not inventory:
        raise DeployError("source_tree_empty")
    return digest(canonical(sorted(inventory)))


def _identity(owner, fingerprint=None, generation=None):
    if not isinstance(owner, str) or not re.fullmatch(r"[a-zA-Z0-9_.:-]{1,128}", owner):
        raise DeployError("image_owner_invalid")
    if fingerprint is not None and (not isinstance(fingerprint, str) or not re.fullmatch(r"[a-f0-9]{64}", fingerprint)):
        raise DeployError("image_source_fingerprint_invalid")
    if generation is not None and (not isinstance(generation, str) or not re.fullmatch(r"[a-zA-Z0-9_.:-]{1,128}", generation)):
        raise DeployError("image_generation_invalid")


def _inspect(runner, reference):
    if not isinstance(reference, str) or not reference or len(reference) > 512 or reference.startswith("-") or any(char.isspace() for char in reference):
        raise DeployError("image_reference_invalid")
    image = json_output(runner, ["docker", "image", "inspect", "--format", "{{json .}}", reference],
                        code="required_local_image_unavailable")
    if (not isinstance(image, dict) or not isinstance(image.get("Id"), str)
            or not IMAGE_ID.fullmatch(image["Id"])):
        raise DeployError("image_inspect_result_invalid")
    return image


def _verified(runner, reference, labels):
    image = _inspect(runner, reference)
    config = image.get("Config")
    if not isinstance(config, dict):
        raise DeployError("image_inspect_result_invalid")
    actual = config.get("Labels") or {}
    if not isinstance(actual, dict) or any(actual.get(key) != value for key, value in labels.items()):
        raise DeployError("local_image_owner_or_source_mismatch")
    return image["Id"]


def build_waf(runner, settings, mode, fingerprint, owner):
    """Build current WAF source or accept only exactly source-bound local images."""
    _identity(owner, fingerprint=fingerprint)
    if mode not in {"build", "local"}:
        raise DeployError("image_build_mode_invalid")
    labels = {OWNER_LABEL: owner, SOURCE_LABEL: fingerprint}
    if mode == "local":
        values = settings["values"]
        references = {"backend": values.get("WAF_BACKEND_IMAGE"), "frontend": values.get("WAF_FRONTEND_IMAGE")}
        if not all(references.values()):
            raise DeployError("local_mode_explicit_images_required")
        return {role: _verified(runner, reference, labels) for role, reference in references.items()}
    root = Path(settings["root"])
    if source_fingerprint(root) != fingerprint:
        raise DeployError("source_changed_before_build")
    references = {}
    for role, context, dockerfile in (("backend", root / "backend", root / "backend/Dockerfile"),
                                      ("frontend", root, root / "frontend/Dockerfile")):
        regular_file(dockerfile)
        reference = "waf-ai-console-" + role + ":build-" + digest(owner)[:12] + "-" + fingerprint[:24]
        args = ["docker", "build", "--pull=false", "--tag", reference,
                "--label", OWNER_LABEL + "=" + owner, "--label", SOURCE_LABEL + "=" + fingerprint,
                "--file", str(dockerfile), str(context)]
        runner.run(args, code="waf_image_build_failed", timeout=3600)
        references[role] = _verified(runner, reference, labels)
    if source_fingerprint(root) != fingerprint:
        raise DeployError("source_changed_during_build")
    return references


def build_gateway(runner, directory, base_image, owner, generation):
    """Build COPY-only derivative of the exact running image using a WAF tag.

Image configuration IDs are not registry manifest digests. Give the local
base ID a WAF-owned tag so Docker's local builder can use it without guessing
a registry reference. The team's original image tag is never changed.
"""
    _identity(owner, generation=generation)
    if not isinstance(base_image, str) or not IMAGE_ID.fullmatch(base_image):
        raise DeployError("gateway_base_image_id_required")
    context = Path(directory) / "gateway-build"
    if not context.is_dir() or any(path.is_symlink() for path in (context, *context.parents)):
        raise DeployError("gateway_build_context_invalid")
    if {path.name for path in context.iterdir()} != {"Dockerfile", "server.conf.template"}:
        raise DeployError("gateway_build_context_files_invalid")
    expected_dockerfile = ("ARG BASE_IMAGE\nFROM ${BASE_IMAGE}\n"
                           "COPY --chmod=0444 server.conf.template /etc/platform-gateway/server.conf.template\n")
    try:
        dockerfile = regular_file(context / "Dockerfile", private=True).read_text(encoding="utf-8")
        regular_file(context / "server.conf.template", private=True)
    except (OSError, UnicodeError):
        raise DeployError("gateway_build_context_unavailable") from None
    if dockerfile != expected_dockerfile:
        raise DeployError("gateway_dockerfile_not_copy_only")
    if _inspect(runner, base_image)["Id"] != base_image:
        raise DeployError("gateway_base_image_changed")
    base_tag = "waf-ai-console-gateway-base:" + digest(owner)[:12] + "-" + base_image[7:]
    # The full source ID in this unique WAF-owned tag avoids changing any team
    # image reference or relying on mutable production/latest tags.
    runner.run(["docker", "image", "tag", base_image, base_tag], code="gateway_base_image_preserve_failed")
    if _inspect(runner, base_tag)["Id"] != base_image:
        raise DeployError("gateway_base_image_changed")
    tag = "waf-ai-console-gateway:managed-" + digest(owner)[:12] + "-" + digest(generation)[:24]
    labels = {OWNER_LABEL: owner, GENERATION_LABEL: generation}
    runner.run(["docker", "build", "--pull=false", "--network=none", "--tag", tag,
                "--label", OWNER_LABEL + "=" + owner, "--label", GENERATION_LABEL + "=" + generation,
                "--build-arg", "BASE_IMAGE=" + base_tag, "--file", str(context / "Dockerfile"), str(context)],
               code="gateway_image_build_failed", timeout=300)
    return _verified(runner, tag, labels)
