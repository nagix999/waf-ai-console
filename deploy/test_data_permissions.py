"""Synthetic directory checks only; never attach a live WAF volume."""
from contextlib import redirect_stdout, redirect_stderr
import io
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from deploy import data_permissions


class DataPermissionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name) / "data"
        self.directory.mkdir()

    def prepare(self):
        data_permissions.prepare_directory(self.directory, os.getuid(), os.getgid())

    def test_empty_directory_and_sqlite_files_keep_contents(self):
        self.prepare()
        for name in data_permissions.DATABASE_FILES:
            (self.directory / name).write_bytes(b"synthetic-sqlite-bytes")
        self.prepare()
        self.assertEqual(stat.S_IMODE(self.directory.stat().st_mode), 0o700)
        for item in self.directory.iterdir():
            self.assertEqual(item.read_bytes(), b"synthetic-sqlite-bytes")
            self.assertEqual(stat.S_IMODE(item.stat().st_mode), 0o600)

    def test_nonempty_directory_without_main_database_refused_before_any_changes(self):
        for name in ("other.db", "backups", "waf.db-wal", "waf.db-shm", "waf.db-journal"):
            with self.subTest(name=name):
                target = self.directory / name
                target.mkdir() if name == "backups" else target.touch()
                try:
                    with patch.object(os, "fchown") as chown, patch.object(os, "fchmod") as chmod, self.assertRaisesRegex(ValueError, "data_volume_database_required"):
                        self.prepare()
                    chown.assert_not_called()
                    chmod.assert_not_called()
                finally:
                    target.rmdir() if target.is_dir() else target.unlink()

    def test_existing_backups_and_unrelated_entries_are_not_opened_or_changed(self):
        for name in data_permissions.DATABASE_FILES:
            (self.directory / name).write_bytes(b"current-synthetic-sqlite-bytes")
        backup_directory = self.directory / "backups"
        nested = backup_directory / "old"
        nested.mkdir(parents=True)
        backup = self.directory / "waf-before-synthetic.db"
        backup.write_bytes(b"preserve-backup-bytes")
        backup.chmod(0o640)
        (nested / "waf.db").write_bytes(b"preserve-nested-backup-bytes")
        nested.chmod(0o750)
        outside = self.directory.parent / "preserve"
        outside.write_bytes(b"preserve-external-bytes")
        (self.directory / "external-link").symlink_to(outside)
        (self.directory / "backup-link").symlink_to(backup_directory, target_is_directory=True)
        os.link(outside, self.directory / "other-hardlink.db")
        os.mkfifo(self.directory / "unrelated-fifo")

        def preserved_state():
            entries = [backup, backup_directory, nested, nested / "waf.db", outside,
                       self.directory / "external-link", self.directory / "backup-link",
                       self.directory / "other-hardlink.db", self.directory / "unrelated-fifo"]
            state = {}
            for entry in entries:
                info = entry.lstat()
                content = (entry.read_bytes() if stat.S_ISREG(info.st_mode) else
                           os.readlink(entry) if stat.S_ISLNK(info.st_mode) else None)
                state[str(entry)] = (info.st_mode, info.st_uid, info.st_gid,
                                     info.st_nlink, info.st_size, content)
            return state

        before = preserved_state()
        with patch.object(os, "open", wraps=os.open) as opened:
            self.prepare()
        self.assertEqual(preserved_state(), before)
        opened_names = [call.args[0] for call in opened.call_args_list]
        self.assertEqual(opened_names, [self.directory, *sorted(data_permissions.DATABASE_FILES)])
        directory_descriptor = opened.call_args_list[1].kwargs["dir_fd"]
        for call in opened.call_args_list[1:]:
            self.assertEqual(call.kwargs["dir_fd"], directory_descriptor)
            self.assertTrue(call.args[1] & os.O_NOFOLLOW)
        self.assertEqual(stat.S_IMODE(self.directory.stat().st_mode), 0o700)
        for name in data_permissions.DATABASE_FILES:
            target = self.directory / name
            self.assertEqual(target.read_bytes(), b"current-synthetic-sqlite-bytes")
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)

    def test_symlinks_hardlinks_fifo_and_directories_refused(self):
        target = self.directory / "waf.db"
        outside = self.directory.parent / "preserve"
        outside.write_bytes(b"preserve")
        for kind in ("symlink", "hardlink", "fifo", "directory"):
            with self.subTest(kind=kind):
                if kind == "symlink":
                    target.symlink_to(outside)
                elif kind == "hardlink":
                    os.link(outside, target)
                elif kind == "fifo":
                    os.mkfifo(target)
                else:
                    target.mkdir()
                try:
                    with patch.object(os, "fchown") as chown, patch.object(os, "fchmod") as chmod, self.assertRaises((ValueError, OSError)):
                        self.prepare()
                    chown.assert_not_called()
                    chmod.assert_not_called()
                    self.assertEqual(outside.read_bytes(), b"preserve")
                finally:
                    target.rmdir() if kind == "directory" else target.unlink()

    def test_invalid_sidecar_refused_after_main_database_validated(self):
        (self.directory / "waf.db").write_bytes(b"preserve-main-database")
        for name in sorted(data_permissions.DATABASE_FILES - {"waf.db"}):
            for kind in ("symlink", "hardlink", "fifo", "directory"):
                with self.subTest(name=name, kind=kind):
                    target = self.directory / name
                    if kind == "symlink":
                        target.symlink_to(self.directory / "waf.db")
                    elif kind == "hardlink":
                        outside = self.directory.parent / "sidecar-preserve"
                        outside.write_bytes(b"preserve-sidecar-target")
                        os.link(outside, target)
                    elif kind == "fifo":
                        os.mkfifo(target)
                    else:
                        target.mkdir()
                    try:
                        with patch.object(os, "fchown") as chown, patch.object(os, "fchmod") as chmod, self.assertRaises((ValueError, OSError)):
                            self.prepare()
                        chown.assert_not_called()
                        chmod.assert_not_called()
                        self.assertEqual((self.directory / "waf.db").read_bytes(), b"preserve-main-database")
                    finally:
                        target.rmdir() if kind == "directory" else target.unlink()

    def test_data_directory_symlink_refused(self):
        link = self.directory.parent / "link"
        link.symlink_to(self.directory, target_is_directory=True)
        with self.assertRaises(OSError):
            data_permissions.prepare_directory(link)

    def test_cli_requires_exact_acknowledgment_root_and_mount(self):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()), patch.object(data_permissions, "prepare_directory") as prepare:
            for args, uid, mounted in (([], 0, True), (["/arbitrary/path"], 0, True),
                                      (["--stopped-and-backed-up"], 1000, True),
                                      (["--stopped-and-backed-up"], 0, False)):
                with patch.object(os, "geteuid", return_value=uid), patch.object(os.path, "ismount", return_value=mounted):
                    self.assertEqual(data_permissions.main(args), 1)
            prepare.assert_not_called()
            with patch.object(os, "geteuid", return_value=0), patch.object(os.path, "ismount", return_value=True):
                self.assertEqual(data_permissions.main(["--stopped-and-backed-up"]), 0)
            prepare.assert_called_once_with(Path("/data"))


if __name__ == "__main__":
    unittest.main()
