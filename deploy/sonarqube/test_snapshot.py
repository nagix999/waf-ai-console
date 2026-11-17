"""Offline safety tests for the SonarScanner source-only snapshot."""

from __future__ import annotations

import hashlib
import importlib.util
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch


_SPEC = importlib.util.spec_from_file_location("sonarqube_snapshot", Path(__file__).with_name("snapshot.py"))
snapshot = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(snapshot)


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.repository = self.base / "repository"
        self.repository.mkdir()
        self.target = self.base / "snapshot"
        self.write("backend/app/main.py", "print('synthetic source')\n")

    def write(self, relative, content="synthetic\n"):
        path = self.repository / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def build(self):
        return snapshot.build_snapshot(self.repository, self.target)

    def test_allowlist_preserves_production_schemas_and_separates_tests(self):
        selected = [
            "backend/app/test_run_schemas.py", "backend/app/api/test_runs.py",
            "backend/tests/test_api.py", "backend/alembic/versions/0001.py",
            "backend/scripts/check.py", "frontend/src/App.jsx",
            "frontend/src/features/table.test.mjs", "frontend/src/contract.js",
            "frontend/scripts/build.mjs", "deploy/automation/test_runtime.py",
            "deploy/sonarqube/snapshot.py", "scripts/nested/test_cli.py",
            "scripts/reportlab_preview/output.test.mjs",
            "scripts/deploy.sh", "backend/Dockerfile", "frontend/Dockerfile",
            "docker-compose.yml", "docker-compose.production.yml",
            "frontend/package.json", "frontend/vite.config.js",
            "backend/pyproject.toml", "backend/alembic.ini",
        ]
        for path in selected:
            self.write(path)
        result = self.build()
        files = {item["path"]: item for item in result["files"]}
        self.assertEqual(set(files), set(selected) | {"backend/app/main.py"})
        for path in ("backend/app/test_run_schemas.py", "backend/app/api/test_runs.py", "frontend/src/contract.js"):
            self.assertEqual(files[path]["kind"], "source")
        for path in ("backend/tests/test_api.py", "frontend/src/features/table.test.mjs", "deploy/automation/test_runtime.py", "scripts/nested/test_cli.py", "scripts/reportlab_preview/output.test.mjs"):
            self.assertEqual(files[path]["kind"], "test")
        properties = (self.target / snapshot.PROPERTIES_FILE).read_text()
        entries = dict(line.split("=", 1) for line in properties.splitlines())
        sources, tests = set(entries["sonar.sources"].split(",")), set(entries["sonar.tests"].split(","))
        self.assertFalse(sources & tests)
        self.assertEqual(sources | tests, set(files))
        self.assertEqual(entries["sonar.python.version"], "3.12")
        self.assertEqual(entries["sonar.scm.disabled"], "true")
        self.assertNotIn("coverage", properties)

    def test_environment_secrets_generated_data_and_non_code_are_excluded(self):
        excluded = [
            ".env", ".env.production", "backend/app/.env.local",
            "backend/app/secrets.yaml", "backend/app/credentials.yml",
            "backend/app/id_rsa.py", "backend/app/private_key.yml",
            "backend/app/config.secret.yml", "backend/app/key.pem",
            "backend/app/database.sqlite", "backend/app/access.log",
            "backend/app/data/report.py", "backend/app/data/report.html",
            "backend/app/reports/generated.html", "frontend/src/.env.js",
            "frontend/src/node_modules/dependency.js", "frontend/src/dist/app.js",
            "frontend/src/test-results/report.html", "deploy/.local-sonarqube/private.py",
            "deploy/.local-deploy/private.py", "deploy/.git/config.py",
            "deploy/cache/module.py", "deploy/package.egg-info/example.py",
            "scripts/sample/fixture.py", "scripts/samples/fixture.py",
            "scripts/docs/example.py", "scripts/mockup/page.html",
            "scripts/WAF_AI_Console_Astra_Handoff_R5/example.py",
            "frontend/package-lock.json", "unlisted/source.py",
        ]
        for path in excluded:
            self.write(path, "SYNTHETIC_SECRET_MARKER\n")
        result = self.build()
        self.assertEqual([item["path"] for item in result["files"]], ["backend/app/main.py"])
        self.assertNotIn("SYNTHETIC_SECRET_MARKER", str(result))
        for path in excluded:
            self.assertFalse((self.target / path).exists(), path)

    def test_content_hash_counts_posix_paths_and_read_permissions(self):
        self.target.mkdir(mode=0o700)
        result = self.build()
        entry = result["files"][0]
        original = (self.repository / entry["path"]).read_bytes()
        self.assertEqual(entry["sha256"], hashlib.sha256(original).hexdigest())
        self.assertEqual(result["total_bytes"], len(original))
        self.assertEqual(result["file_count"], 1)
        self.assertEqual(result["source_file_count"], 1)
        self.assertEqual(result["test_file_count"], 0)
        self.assertEqual(stat.S_IMODE((self.target / entry["path"]).stat().st_mode), 0o644)
        for path in (self.target, self.target / "backend", self.target / "backend/app"):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o755)
        self.assertNotIn("sonar.tests=", (self.target / snapshot.PROPERTIES_FILE).read_text())

    def test_existing_files_are_never_overwritten_even_in_hidden_destination(self):
        self.target.mkdir()
        existing = self.target / ".preserve"
        existing.write_text("preserve")
        with self.assertRaisesRegex(snapshot.SnapshotError, "destination_not_empty"):
            self.build()
        self.assertEqual(existing.read_text(), "preserve")
        self.assertEqual(list(self.target.iterdir()), [existing])

    def test_successful_destination_cannot_be_reused(self):
        first = self.build()
        with self.assertRaisesRegex(snapshot.SnapshotError, "destination_not_empty"):
            self.build()
        self.assertEqual((self.target / first["files"][0]["path"]).read_bytes(), (self.repository / "backend/app/main.py").read_bytes())

    def test_symlink_file_is_refused_without_following_it(self):
        secret = self.base / "private.py"
        secret.write_text("SYNTHETIC_SECRET")
        (self.repository / "backend/app/linked.py").symlink_to(secret)
        with self.assertRaisesRegex(snapshot.SnapshotError, "source_symlink"):
            self.build()
        self.assertFalse(self.target.exists())

    def test_symlink_directory_is_refused_even_when_name_is_excluded(self):
        for name in ("linked", ".env-hidden", "node_modules"):
            with self.subTest(name=name):
                link = self.repository / "backend/app" / name
                link.symlink_to(self.base, target_is_directory=True)
                try:
                    with self.assertRaisesRegex(snapshot.SnapshotError, "source_symlink"):
                        self.build()
                finally:
                    link.unlink()

    def test_source_root_or_its_parent_cannot_be_symlink(self):
        real_backend = self.repository / "backend"
        replacement = self.base / "replacement"
        real_backend.rename(replacement)
        real_backend.symlink_to(replacement, target_is_directory=True)
        with self.assertRaisesRegex(snapshot.SnapshotError, "source_symlink_or_invalid_directory"):
            self.build()

    def test_explicit_file_symlink_is_refused(self):
        (self.repository / "docker-compose.yml").symlink_to(self.repository / "backend/app/main.py")
        with self.assertRaisesRegex(snapshot.SnapshotError, "source_symlink"):
            self.build()

    def test_destination_symlink_or_symlink_parent_is_refused(self):
        real = self.base / "real"
        real.mkdir()
        self.target.symlink_to(real, target_is_directory=True)
        with self.assertRaises(snapshot.SnapshotError):
            self.build()
        self.target.unlink()
        parent = self.base / "linked-parent"
        parent.symlink_to(real, target_is_directory=True)
        with self.assertRaises(snapshot.SnapshotError):
            snapshot.build_snapshot(self.repository, parent / "new")
        self.assertEqual(list(real.iterdir()), [])

    def test_destination_file_and_source_overlap_are_refused(self):
        self.target.write_text("preserve")
        with self.assertRaises(snapshot.SnapshotError):
            self.build()
        self.assertEqual(self.target.read_text(), "preserve")
        for target in (self.repository, self.base, self.repository / "deploy/output"):
            with self.subTest(target=target), self.assertRaisesRegex(snapshot.SnapshotError, "destination_overlaps"):
                snapshot.build_snapshot(self.repository, target)

    def test_parent_traversal_is_refused(self):
        with self.assertRaisesRegex(snapshot.SnapshotError, "parent_path_component"):
            snapshot.build_snapshot(self.repository, self.base / "unused/../snapshot")

    def test_local_snapshot_output_is_allowed_outside_source_roots(self):
        self.target = self.repository / ".local-sonarqube" / "snapshot"
        self.target.parent.mkdir(mode=0o700)
        result = self.build()
        self.assertEqual(result["file_count"], 1)

    def test_public_parent_is_refused_before_source_access_and_not_chmodded(self):
        parent = self.base / "public"
        parent.mkdir(mode=0o755)
        parent.chmod(0o755)
        self.target = parent / "snapshot"
        with patch.object(snapshot, "_collect_sources") as collect, patch.object(snapshot, "_read_source") as read:
            with self.assertRaisesRegex(snapshot.SnapshotError, "destination_parent_requires_owned_0700"):
                self.build()
            collect.assert_not_called()
            read.assert_not_called()
        self.assertFalse(self.target.exists())
        self.assertEqual(stat.S_IMODE(parent.stat().st_mode), 0o755)

    def test_private_parent_owned_by_another_user_is_refused(self):
        with patch.object(snapshot.os, "getuid", return_value=os.getuid() + 1):
            with self.assertRaisesRegex(snapshot.SnapshotError, "destination_parent_requires_owned_0700"):
                self.build()
        self.assertFalse(self.target.exists())
        self.assertEqual(stat.S_IMODE(self.base.stat().st_mode), 0o700)

    def test_missing_parent_is_not_automatically_created(self):
        self.target = self.base / "missing" / "snapshot"
        with patch.object(snapshot, "_collect_sources") as collect:
            with self.assertRaisesRegex(snapshot.SnapshotError, "unsafe_or_unavailable_directory"):
                self.build()
            collect.assert_not_called()
        self.assertFalse(self.target.parent.exists())

    def test_parent_replacement_cannot_redirect_copies_to_public_directory(self):
        parent = self.base / "private"
        parent.mkdir(mode=0o700)
        moved = self.base / "moved-private"
        self.target = parent / "snapshot"
        original_collect = snapshot._collect_sources

        def replace_parent(root_descriptor):
            sources = original_collect(root_descriptor)
            parent.rename(moved)
            parent.mkdir(mode=0o755)
            parent.chmod(0o755)
            return sources

        with patch.object(snapshot, "_collect_sources", replace_parent):
            result = self.build()
        self.assertEqual(result["file_count"], 1)
        self.assertEqual(list(parent.iterdir()), [])
        self.assertEqual(stat.S_IMODE(moved.stat().st_mode), 0o700)
        self.assertTrue((moved / "snapshot/backend/app/main.py").is_file())

    def test_limits_are_checked_before_copying(self):
        for setting, value, code in (("MAX_FILE_BYTES", 2, "source_file_size_limit"), ("MAX_TOTAL_BYTES", 2, "snapshot_size_limit"), ("MAX_FILES", 0, "snapshot_file_count_limit")):
            with self.subTest(setting=setting), patch.object(snapshot, setting, value):
                with self.assertRaisesRegex(snapshot.SnapshotError, code):
                    self.build()
                self.assertFalse(self.target.exists())

    def test_binary_disguised_as_source_is_refused(self):
        self.write("backend/app/binary.py", "\x00synthetic")
        with self.assertRaisesRegex(snapshot.SnapshotError, "non_text_source_file"):
            self.build()

    def test_unsupported_properties_path_is_refused(self):
        self.write("backend/app/ambiguous,path.py")
        with self.assertRaisesRegex(snapshot.SnapshotError, "unsupported_source_path"):
            self.build()

    def test_source_replacement_during_copy_is_refused(self):
        original_reader = snapshot._read_source

        def changed_reader(root_descriptor, source):
            (self.repository / source.path).write_text("changed source\n")
            return original_reader(root_descriptor, source)

        with patch.object(snapshot, "_read_source", changed_reader):
            with self.assertRaisesRegex(snapshot.SnapshotError, "source_changed_during_snapshot"):
                self.build()


if __name__ == "__main__":
    unittest.main()
