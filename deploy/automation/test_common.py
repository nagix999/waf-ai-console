import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from .common import DeployError, Runner, operator_lock, private_json, private_write, secure_dir


class CommonTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_all_created_directories_private(self):
        path = secure_dir(self.root / "one/two/three")
        for value in (path, path.parent, path.parent.parent):
            self.assertEqual(value.stat().st_mode & 0o777, 0o700)

    def test_private_writes_atomic_and_private(self):
        path = self.root / "nested/state.json"
        private_write(path, {"secret": "synthetic-only"})
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(private_json(path), {"secret": "synthetic-only"})
        private_write(path, {"next": 2})
        self.assertEqual(private_json(path), {"next": 2})
        self.assertEqual(list(path.parent.glob(".pending-*")), [])

    def test_symlink_directory_never_followed(self):
        target = self.root / "real"
        target.mkdir()
        link = self.root / "link"
        link.symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(DeployError, "symlink_directory"):
            private_write(link / "state.json", {})
        self.assertFalse((target / "state.json").exists())

    def test_symlink_file_never_overwritten(self):
        target = self.root / "original"
        target.write_text("preserve")
        link = self.root / "link"
        link.symlink_to(target)
        with self.assertRaisesRegex(DeployError, "symlink_file"):
            private_write(link, "new")
        self.assertEqual(target.read_text(), "preserve")

    def test_existing_unprivate_directory_refused(self):
        path = self.root / "public"
        path.mkdir(mode=0o755)
        with self.assertRaisesRegex(DeployError, "private_directory_required"):
            secure_dir(path)

    def test_existing_lock_never_created(self):
        path = self.root / "team/missing.lock"
        with self.assertRaisesRegex(DeployError, "operator_lock_busy_or_unavailable"):
            with operator_lock(path, create=False):
                self.fail("missing lock acquired")
        self.assertFalse(path.parent.exists())

    def test_existing_lock_never_truncated(self):
        path = self.root / "operator.lock"
        path.write_text("team-owned-content")
        with operator_lock(path, create=False):
            self.assertEqual(path.read_text(), "team-owned-content")
        self.assertEqual(path.read_text(), "team-owned-content")

    def test_lock_excludes_other_operator_and_releases(self):
        path = self.root / "operator.lock"
        with operator_lock(path):
            with self.assertRaisesRegex(DeployError, "operator_lock_busy"):
                with operator_lock(path):
                    self.fail("double lock")
        with operator_lock(path):
            pass

    def test_lock_preserves_body_error_not_misreported_as_lock_failure(self):
        path = self.root / "operator.lock"
        with self.assertRaisesRegex(OSError, "synthetic-body-error"):
            with operator_lock(path):
                raise OSError("synthetic-body-error")
        with operator_lock(path):
            pass

    def test_runner_errors_do_not_include_output_or_argument_secrets(self):
        runner = Runner()
        result = subprocess.CompletedProcess(["synthetic"], 1, "secret-stdout", "secret-stderr")
        with patch("subprocess.run", return_value=result):
            with self.assertRaises(DeployError) as caught:
                runner.run(["synthetic", "private-argument"], code="safe_code")
        self.assertEqual(str(caught.exception), "safe_code")

    def test_ambient_compose_and_waf_environment_removed(self):
        with patch.dict(os.environ, {"WAF_ADMIN_PASSWORD": "never-pass", "COMPOSE_FILE": "foreign",
                                     "PLATFORM_MODE": "foreign", "BASH_ENV": "foreign"}):
            environment = Runner().env
        self.assertNotIn("WAF_ADMIN_PASSWORD", environment)
        self.assertNotIn("COMPOSE_FILE", environment)
        self.assertNotIn("PLATFORM_MODE", environment)
        self.assertNotIn("BASH_ENV", environment)

    def test_remote_builder_environment_removed_and_default_pinned(self):
        with patch.dict(os.environ, {"BUILDX_BUILDER": "remote", "BUILDX_CONFIG": "/foreign",
                                     "BUILDKIT_HOST": "tcp://foreign:1234",
                                     "EXPERIMENTAL_BUILDKIT_SOURCE_POLICY": "/foreign-policy"}):
            environment = Runner().env
        self.assertEqual(environment["BUILDX_BUILDER"], "default")
        self.assertNotIn("BUILDX_CONFIG", environment)
        self.assertNotIn("BUILDKIT_HOST", environment)
        self.assertNotIn("EXPERIMENTAL_BUILDKIT_SOURCE_POLICY", environment)

    def test_help_needs_no_env_docker_or_state_write(self):
        root = Path(__file__).resolve().parents[2]
        result = subprocess.run([str(root / "scripts/deploy.sh"), "--help"], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("{check,deploy,status,rollback}", result.stdout)


if __name__ == "__main__":
    unittest.main()
