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

    def test_unrelated_entry_refused_before_any_changes(self):
        (self.directory / "waf.db").touch()
        (self.directory / "other.db").touch()
        with patch.object(os, "fchown") as chown, self.assertRaises(ValueError):
            self.prepare()
        chown.assert_not_called()

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
                    with patch.object(os, "fchown") as chown, self.assertRaises((ValueError, OSError)):
                        self.prepare()
                    chown.assert_not_called()
                    self.assertEqual(outside.read_bytes(), b"preserve")
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
