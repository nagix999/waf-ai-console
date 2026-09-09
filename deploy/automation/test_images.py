"""Image workflow mocks; tests never build/pull images or contact Docker."""
import json
import os
from pathlib import Path
import tempfile
import unittest

from .common import DeployError
from .images import GENERATION_LABEL, OWNER_LABEL, SOURCE_LABEL, build_gateway, build_waf, source_fingerprint


OWNER = "fixture-owner-123456"
GENERATION = "fixture-generation-1"
BASE_ID = "sha256:" + "a" * 64
DERIVED_ID = "sha256:" + "b" * 64
FRONTEND_ID = "sha256:" + "c" * 64


class FakeRunner:
    def __init__(self):
        self.calls = []
        self.images = {}
        self.fail_build = False
        self.after_build = None

    def image(self, reference, identity=BASE_ID, labels=None):
        self.images[reference] = {"Id": identity, "Config": {"Labels": labels or {}}}

    def run(self, args, **kwargs):
        args = [str(arg) for arg in args]
        self.calls.append(args)
        if args[:3] == ["docker", "image", "inspect"]:
            if args[-1] not in self.images:
                raise DeployError("required_local_image_unavailable")
            return json.dumps(self.images[args[-1]])
        if args[:3] == ["docker", "image", "tag"]:
            self.images[args[-1]] = self.images[args[-2]]
            return ""
        if args[:2] == ["docker", "build"]:
            if self.fail_build:
                raise DeployError("fixture_build_failed")
            tag = args[args.index("--tag") + 1]
            labels = dict(args[index + 1].split("=", 1) for index, value in enumerate(args) if value == "--label")
            self.image(tag, FRONTEND_ID if "frontend" in tag else DERIVED_ID, labels)
            if self.after_build:
                self.after_build()
            return "fixture build output is private"
        raise AssertionError("unexpected command in synthetic runner")


class ImageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="waf-image-unit-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "backend").mkdir()
        (self.root / "frontend").mkdir()
        (self.root / "backend/Dockerfile").write_text("FROM fixture:never-pulled\n")
        (self.root / "frontend/Dockerfile").write_text("FROM fixture:never-pulled\n")
        self.settings = {"root": str(self.root), "values": {}}
        self.runner = FakeRunner()

    def test_source_fingerprint_stable(self):
        self.assertEqual(source_fingerprint(self.root), source_fingerprint(self.root))
        self.assertEqual(len(source_fingerprint(self.root)), 64)

    def test_source_change_changes_fingerprint(self):
        before = source_fingerprint(self.root)
        (self.root / "backend/app.py").write_text("# fixture source\n")
        self.assertNotEqual(before, source_fingerprint(self.root))

    def test_source_rename_changes_fingerprint(self):
        source = self.root / "backend/app.py"
        source.write_text("fixture")
        before = source_fingerprint(self.root)
        source.rename(source.with_name("other.py"))
        self.assertNotEqual(before, source_fingerprint(self.root))

    def test_executable_permissions_change_fingerprint(self):
        source = self.root / "backend/app.py"
        source.write_text("fixture")
        source.chmod(0o600)
        before = source_fingerprint(self.root)
        source.chmod(0o700)
        self.assertNotEqual(before, source_fingerprint(self.root))

    def test_secrets_and_generated_trees_are_not_read(self):
        before = source_fingerprint(self.root)
        for name in (".env", ".env.production", "key.pem", "secret.key", "data.db", "data.db-wal", "app.pyc"):
            (self.root / name).write_text("private-value-must-not-change-source")
        for name in ("node_modules", ".venv", ".git", ".local-deploy", "__pycache__", "dist"):
            directory = self.root / name
            directory.mkdir()
            (directory / "secret").write_text("private-value")
        self.assertEqual(before, source_fingerprint(self.root))

    def test_source_symlink_rejected(self):
        (self.root / "backend/link.py").symlink_to(self.root / "backend/Dockerfile")
        with self.assertRaises(DeployError):
            source_fingerprint(self.root)

    def test_source_directory_symlink_rejected(self):
        (self.root / "backend/linked").symlink_to(self.root / "frontend", target_is_directory=True)
        with self.assertRaises(DeployError):
            source_fingerprint(self.root)

    def test_source_fifo_rejected_without_opening_it(self):
        os.mkfifo(self.root / "backend/not-source")
        with self.assertRaises(DeployError):
            source_fingerprint(self.root)

    def test_build_prepares_two_images_with_source_and_owner_labels(self):
        fingerprint = source_fingerprint(self.root)
        result = build_waf(self.runner, self.settings, "build", fingerprint, OWNER)
        self.assertEqual(result, {"backend": DERIVED_ID, "frontend": FRONTEND_ID})
        builds = [call for call in self.runner.calls if call[:2] == ["docker", "build"]]
        self.assertEqual(len(builds), 2)
        self.assertEqual(builds[0][-1], str(self.root / "backend"))
        self.assertEqual(builds[1][-1], str(self.root))
        for call in builds:
            self.assertIn(OWNER_LABEL + "=" + OWNER, call)
            self.assertIn(SOURCE_LABEL + "=" + fingerprint, call)
            self.assertNotIn("--push", call)
            self.assertNotIn("--no-cache", call)

    def test_local_uses_only_inspect_and_requires_matching_labels(self):
        fingerprint = source_fingerprint(self.root)
        self.settings["values"].update(WAF_BACKEND_IMAGE="fixture-backend:local", WAF_FRONTEND_IMAGE="fixture-frontend:local")
        labels = {OWNER_LABEL: OWNER, SOURCE_LABEL: fingerprint}
        self.runner.image("fixture-backend:local", DERIVED_ID, labels)
        self.runner.image("fixture-frontend:local", FRONTEND_ID, labels)
        self.assertEqual(build_waf(self.runner, self.settings, "local", fingerprint, OWNER),
                         {"backend": DERIVED_ID, "frontend": FRONTEND_ID})
        self.assertTrue(all(call[:3] == ["docker", "image", "inspect"] for call in self.runner.calls))

    def test_local_unlabelled_or_old_images_rejected(self):
        fingerprint = source_fingerprint(self.root)
        self.settings["values"].update(WAF_BACKEND_IMAGE="fixture-backend:local", WAF_FRONTEND_IMAGE="fixture-frontend:local")
        for labels in ({}, {OWNER_LABEL: OWNER, SOURCE_LABEL: "0" * 64}, {OWNER_LABEL: "other", SOURCE_LABEL: fingerprint}):
            self.runner.image("fixture-backend:local", DERIVED_ID, labels)
            with self.subTest(labels=labels), self.assertRaisesRegex(DeployError, "mismatch"):
                build_waf(self.runner, self.settings, "local", fingerprint, OWNER)

    def test_local_requires_explicit_images(self):
        with self.assertRaisesRegex(DeployError, "explicit_images"):
            build_waf(self.runner, self.settings, "local", source_fingerprint(self.root), OWNER)
        self.assertEqual(self.runner.calls, [])

    def test_source_changed_before_build_rejected(self):
        with self.assertRaisesRegex(DeployError, "source_changed_before"):
            build_waf(self.runner, self.settings, "build", "0" * 64, OWNER)
        self.assertEqual(self.runner.calls, [])

    def test_source_changed_during_build_rejected(self):
        fingerprint = source_fingerprint(self.root)
        self.runner.after_build = lambda: (self.root / "changed.py").write_text("fixture change")
        with self.assertRaisesRegex(DeployError, "source_changed_during"):
            build_waf(self.runner, self.settings, "build", fingerprint, OWNER)

    def test_build_failure_does_not_start_or_remove_services(self):
        self.runner.fail_build = True
        with self.assertRaises(DeployError):
            build_waf(self.runner, self.settings, "build", source_fingerprint(self.root), OWNER)
        self.assertTrue(all(call[:2] == ["docker", "build"] for call in self.runner.calls))

    def context(self):
        directory = self.root / ".local-deploy/generations/fixture"
        context = directory / "gateway-build"
        context.mkdir(mode=0o700, parents=True)
        dockerfile = ("ARG BASE_IMAGE\nFROM ${BASE_IMAGE}\n"
                      "COPY --chmod=0444 server.conf.template /etc/platform-gateway/server.conf.template\n")
        (context / "Dockerfile").write_text(dockerfile)
        (context / "server.conf.template").write_text("# fixture-only gateway\n")
        for path in context.iterdir():
            path.chmod(0o600)
        self.runner.image(BASE_ID)
        return directory

    def test_gateway_only_adds_private_template_to_preserved_base(self):
        directory = self.context()
        result = build_gateway(self.runner, directory, BASE_ID, OWNER, GENERATION)
        self.assertEqual(result, DERIVED_ID)
        tag_call = next(call for call in self.runner.calls if call[:3] == ["docker", "image", "tag"])
        self.assertEqual(tag_call[-2], BASE_ID)
        self.assertTrue(tag_call[-1].startswith("waf-ai-console-gateway-base:"))
        self.assertIn(BASE_ID[7:], tag_call[-1])
        build = next(call for call in self.runner.calls if call[:2] == ["docker", "build"])
        self.assertIn("--network=none", build)
        self.assertIn("--pull=false", build)
        self.assertIn("BASE_IMAGE=" + tag_call[-1], build)
        self.assertIn(GENERATION_LABEL + "=" + GENERATION, build)
        self.assertEqual(build[-1], str(directory / "gateway-build"))

    def test_gateway_mutable_base_tag_rejected(self):
        with self.assertRaisesRegex(DeployError, "image_id_required"):
            build_gateway(self.runner, self.context(), "team:production", OWNER, GENERATION)
        self.assertEqual(self.runner.calls, [])

    def test_gateway_unknown_build_context_file_rejected(self):
        directory = self.context()
        (directory / "gateway-build/private.env").write_text("fixture-private")
        with self.assertRaisesRegex(DeployError, "context_files"):
            build_gateway(self.runner, directory, BASE_ID, OWNER, GENERATION)
        self.assertEqual(self.runner.calls, [])

    def test_gateway_mutated_dockerfile_rejected(self):
        directory = self.context()
        (directory / "gateway-build/Dockerfile").write_text("FROM fixture\nUSER root\nRUN bad\n")
        with self.assertRaisesRegex(DeployError, "copy_only"):
            build_gateway(self.runner, directory, BASE_ID, OWNER, GENERATION)
        self.assertEqual(self.runner.calls, [])

    def test_gateway_symlinked_template_rejected(self):
        directory = self.context()
        template = directory / "gateway-build/server.conf.template"
        template.unlink()
        template.symlink_to(self.root / "backend/Dockerfile")
        with self.assertRaises(DeployError):
            build_gateway(self.runner, directory, BASE_ID, OWNER, GENERATION)
        self.assertEqual(self.runner.calls, [])

    def test_gateway_unprivate_template_rejected(self):
        directory = self.context()
        (directory / "gateway-build/server.conf.template").chmod(0o644)
        with self.assertRaises(DeployError):
            build_gateway(self.runner, directory, BASE_ID, OWNER, GENERATION)
        self.assertEqual(self.runner.calls, [])

    def test_invalid_identity_rejected_before_commands(self):
        for owner in ("", "owner\nvalue"):
            with self.subTest(owner=owner), self.assertRaises(DeployError):
                build_waf(self.runner, self.settings, "build", source_fingerprint(self.root), owner)
        self.assertEqual(self.runner.calls, [])


if __name__ == "__main__":
    unittest.main()
