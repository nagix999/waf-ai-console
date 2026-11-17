"""Synthetic, non-root tests for the fixed-mount Nginx maintenance helper."""
from contextlib import redirect_stderr, redirect_stdout
import io
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from deploy import web_config_permissions as permissions


class WebConfigPermissionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="waf-web-config-unit-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.policy = self.root / "policy"
        self.policy.mkdir(mode=0o700)
        self.contents = {}
        for name in (*sorted(permissions.POLICY_FILES), permissions.MANIFEST):
            target = self.policy / name
            value = "synthetic config, never a real origin or credential: " + name
            target.write_text(value)
            target.chmod(0o600)
            self.contents[name] = value
        self.changes = []
        self.chown = patch.object(permissions.os, "fchown", side_effect=lambda descriptor, uid, gid: self.changes.append((os.fstat(descriptor).st_ino, uid, gid)))
        self.chown.start()
        self.addCleanup(self.chown.stop)

    def unchanged(self):
        self.assertEqual(self.changes, [])
        self.assertEqual(stat.S_IMODE(self.policy.stat().st_mode), 0o700)
        for name in permissions.POLICY_FILES:
            path = self.policy / name
            if path.is_file() and not path.is_symlink():
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_group_access_preserves_operator_uid_contents_and_private_manifest(self):
        before = {name: (self.policy / name).stat() for name in self.contents}
        permissions.prepare_config(self.policy)
        for name, content in self.contents.items():
            path = self.policy / name
            self.assertEqual(path.read_text(), content)
            self.assertEqual(path.stat().st_uid, before[name].st_uid)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600 if name == permissions.MANIFEST else 0o640)
        self.assertEqual(stat.S_IMODE(self.policy.stat().st_mode), 0o750)
        changed = {entry[0] for entry in self.changes}
        self.assertEqual(changed, {before[name].st_ino for name in permissions.POLICY_FILES} | {self.policy.stat().st_ino})
        self.assertNotIn(before[permissions.MANIFEST].st_ino, changed)
        self.assertTrue(all(uid == -1 and gid == 101 for _, uid, gid in self.changes))

    def test_site_config_is_prepared_without_changing_contents_or_uid(self):
        site = self.root / "site.conf"
        site.write_text("synthetic site config")
        site.chmod(0o600)
        before = site.stat()
        permissions.prepare_config(self.policy, site)
        self.assertEqual(site.read_text(), "synthetic site config")
        self.assertEqual(site.stat().st_uid, before.st_uid)
        self.assertEqual(stat.S_IMODE(site.stat().st_mode), 0o640)
        self.assertIn((before.st_ino, -1, 101), self.changes)

    def test_manifest_is_optional(self):
        (self.policy / permissions.MANIFEST).unlink()
        permissions.prepare_config(self.policy)
        self.assertEqual(len(self.changes), 4)

    def test_missing_required_policy_file_fails_before_any_changes(self):
        (self.policy / "http.conf").unlink()
        with self.assertRaises(ValueError):
            permissions.prepare_config(self.policy)
        self.unchanged()

    def test_unknown_policy_entry_fails_before_any_changes(self):
        (self.policy / "unexpected.env").write_text("synthetic sentinel")
        with self.assertRaises(ValueError):
            permissions.prepare_config(self.policy)
        self.unchanged()

    def test_policy_directory_symlink_is_rejected(self):
        alias = self.root / "alias"
        alias.symlink_to(self.policy, target_is_directory=True)
        with self.assertRaises(OSError):
            permissions.prepare_config(alias)
        self.unchanged()

    def test_policy_file_symlink_is_rejected_before_any_changes(self):
        target = self.policy / "proxy.conf"
        target.unlink()
        target.symlink_to(self.root / "unrelated-missing-file")
        with self.assertRaises(OSError):
            permissions.prepare_config(self.policy)
        self.unchanged()

    def test_policy_hardlink_is_rejected_before_any_changes(self):
        target = self.policy / "proxy.conf"
        os.link(target, self.root / "alias.conf")
        with self.assertRaises(ValueError):
            permissions.prepare_config(self.policy)
        self.unchanged()

    def test_policy_fifo_is_rejected_without_blocking(self):
        target = self.policy / "proxy.conf"
        target.unlink()
        os.mkfifo(target)
        with self.assertRaises(ValueError):
            permissions.prepare_config(self.policy)
        self.unchanged()

    def test_policy_subdirectory_is_rejected_before_any_changes(self):
        target = self.policy / "proxy.conf"
        target.unlink()
        target.mkdir()
        with self.assertRaises(ValueError):
            permissions.prepare_config(self.policy)
        self.unchanged()

    def test_manifest_permissions_are_checked_but_never_repaired(self):
        target = self.policy / permissions.MANIFEST
        target.chmod(0o644)
        with self.assertRaises(ValueError):
            permissions.prepare_config(self.policy)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o644)
        self.unchanged()

    def test_invalid_site_fails_before_policy_metadata_changes(self):
        site = self.root / "site.conf"
        site.symlink_to(self.policy / "http.conf")
        with self.assertRaises(OSError):
            permissions.prepare_config(self.policy, site)
        self.unchanged()

    def test_hardlinked_site_fails_before_policy_metadata_changes(self):
        site = self.root / "site.conf"
        os.link(self.policy / "http.conf", site)
        with self.assertRaises(ValueError):
            permissions.prepare_config(self.policy, site)
        self.unchanged()

    def test_cli_requires_fixed_arguments_root_and_a_policy_mount(self):
        for arguments, uid, mounted in [([], 0, True), (["--prepare", "/other"], 0, True), (["--prepare"], 1000, True), (["--prepare"], 0, False)]:
            with self.subTest(arguments=arguments, uid=uid, mounted=mounted):
                with patch.object(permissions.os, "geteuid", return_value=uid), patch.object(permissions.os.path, "ismount", return_value=mounted), patch.object(permissions, "prepare_config") as prepare, redirect_stderr(io.StringIO()):
                    self.assertEqual(permissions.main(arguments), 1)
                    prepare.assert_not_called()

    def test_cli_prepares_only_fixed_mounts_and_outputs_no_config(self):
        output = io.StringIO()
        with patch.object(permissions.os, "geteuid", return_value=0), patch.object(permissions.os.path, "ismount", return_value=True), patch.object(permissions.os.path, "lexists", return_value=True), patch.object(permissions, "prepare_config") as prepare, redirect_stdout(output):
            self.assertEqual(permissions.main(["--prepare"]), 0)
            prepare.assert_called_once_with(Path("/policy"), Path("/site.conf"))
        self.assertNotIn("synthetic", output.getvalue())

    def test_cli_rejects_an_unmounted_site_and_hides_exception_details(self):
        error = io.StringIO()
        with patch.object(permissions.os, "geteuid", return_value=0), patch.object(permissions.os.path, "ismount", side_effect=lambda path: path == permissions.POLICY_DIRECTORY), patch.object(permissions.os.path, "lexists", return_value=True), patch.object(permissions, "prepare_config") as prepare, redirect_stderr(error):
            self.assertEqual(permissions.main(["--prepare"]), 1)
            prepare.assert_not_called()
        self.assertNotIn("site_config_mount_required", error.getvalue())

    def test_cli_absent_site_is_optional_and_errors_never_echo_contents(self):
        error = io.StringIO()
        with patch.object(permissions.os, "geteuid", return_value=0), patch.object(permissions.os.path, "ismount", return_value=True), patch.object(permissions.os.path, "lexists", return_value=False), patch.object(permissions, "prepare_config", side_effect=ValueError("synthetic secret must not escape")) as prepare, redirect_stderr(error):
            self.assertEqual(permissions.main(["--prepare"]), 1)
            prepare.assert_called_once_with(Path("/policy"), None)
        self.assertNotIn("synthetic secret", error.getvalue())


if __name__ == "__main__":
    unittest.main()
