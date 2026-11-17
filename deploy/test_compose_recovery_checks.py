"""Synthetic read-only tests for checks executed inside the API image."""

import copy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from deploy import compose_recovery_checks as checks


SYNTHETIC_SECRET = "synthetic-secret-never-print"


def configuration():
    return {
        "services": {
            "api": {
                "environment": {"WAF_DATABASE_URL": checks.DATABASE_URL,
                                "API_KEY": SYNTHETIC_SECRET},
                "volumes": [{"type": "volume", "source": "waf-data", "target": "/data"}],
            },
            "worker": {"environment": {"API_KEY": SYNTHETIC_SECRET}},
            "model-tester": {},
            "frontend": {},
        },
        "volumes": {"waf-data": {"name": "waf-standalone_waf-data"}},
        "name": SYNTHETIC_SECRET,
    }


class ComposeRecoveryConfigTests(unittest.TestCase):
    def run_config(self, value, volume="waf-standalone_waf-data", *, raw=False):
        output, errors = io.StringIO(), io.StringIO()
        text = value if raw else json.dumps(value)
        status = checks.main(["config", volume], stdin=io.StringIO(text),
                             stdout=output, stderr=errors)
        self.assertNotIn(SYNTHETIC_SECRET, output.getvalue() + errors.getvalue())
        return status, output.getvalue(), errors.getvalue()

    def test_only_service_names_and_offline_policy_are_emitted(self):
        config = configuration()
        original = copy.deepcopy(config)
        status, output, errors = self.run_config(config)
        self.assertEqual(status, 0)
        self.assertEqual(errors, "")
        self.assertEqual(json.loads(output), {"services": {
            name: {"pull_policy": "never"} for name in config["services"]}})
        self.assertEqual(config, original)

    def test_explicit_false_read_only_is_supported(self):
        config = configuration()
        config["services"]["api"]["volumes"][0]["read_only"] = False
        self.assertEqual(self.run_config(config)[0], 0)

    def test_rejects_wrong_shape_and_invalid_service_definitions(self):
        variants = [None, [], "secret", 42, True, {}, {"services": []},
                    {"services": {}}, {"services": {"worker": {}}}]
        for name, definition in (("api", None), ("api", []), ("bad\nname", {}), ("", {})):
            config = configuration()
            config["services"][name] = definition
            variants.append(config)
        for config in variants:
            with self.subTest(config_type=type(config).__name__):
                status, output, errors = self.run_config(config)
                self.assertEqual(status, 1)
                self.assertEqual(output, "")
                self.assertEqual(errors, checks.CONFIG_ERROR + "\n")

    def test_rejects_malformed_or_duplicate_json_without_echoing_input(self):
        for text in ("{", SYNTHETIC_SECRET, '{"services": {}, "services": {}}',
                     '{"services": {}, "x": NaN}', '{"services": {}, "x": Infinity}'):
            with self.subTest(text_length=len(text)):
                status, output, errors = self.run_config(text, raw=True)
                self.assertEqual(status, 1)
                self.assertEqual(output, "")
                self.assertEqual(errors, checks.CONFIG_ERROR + "\n")

    def test_rejects_missing_ambiguous_or_unsupported_data_mounts(self):
        variants = [None, {}, [], ["waf-data:/data"], [None],
                    [{"type": "volume", "source": "waf-data", "target": "/other"}]]
        good = configuration()["services"]["api"]["volumes"][0]
        variants.append([good, good])
        for changes in ({"type": "bind"}, {"source": None}, {"source": "missing"}):
            variants.append([{**good, **changes}])
        for read_only in (True, 0, 1, "false", "true", None):
            variants.append([{**good, "read_only": read_only}])
        for mounts in variants:
            with self.subTest(mounts_type=type(mounts).__name__):
                config = configuration()
                config["services"]["api"]["volumes"] = mounts
                self.assertEqual(self.run_config(config)[0], 1)

    def test_rejects_missing_different_or_invalid_volume_definition(self):
        for volumes in (None, [], {}, {"waf-data": None}, {"waf-data": {}},
                        {"waf-data": {"name": "other"}}, {"waf-data": {"name": []}}):
            with self.subTest(volumes_type=type(volumes).__name__):
                config = configuration()
                config["volumes"] = volumes
                self.assertEqual(self.run_config(config)[0], 1)
        for volume in ("", "bad\nname", "/data", SYNTHETIC_SECRET + "\n"):
            with self.subTest(volume_length=len(volume)):
                self.assertEqual(self.run_config(configuration(), volume)[0], 1)

    def test_rejects_missing_or_different_database_environment(self):
        for environment in (None, [], {}, ["WAF_DATABASE_URL=" + checks.DATABASE_URL],
                            {"WAF_DATABASE_URL": "sqlite+pysqlite:////other/waf.db"},
                            {"WAF_DATABASE_URL": checks.DATABASE_URL + "?mode=ro"}):
            with self.subTest(environment_type=type(environment).__name__):
                config = configuration()
                config["services"]["api"]["environment"] = environment
                self.assertEqual(self.run_config(config)[0], 1)

    def test_optimized_python_cannot_bypass_configuration_validation(self):
        config = configuration()
        config["services"]["api"]["volumes"][0]["read_only"] = True
        result = subprocess.run(
            [sys.executable, "-I", "-B", "-O", str(Path(checks.__file__).resolve()),
             "config", "waf-standalone_waf-data"], input=json.dumps(config),
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, checks.CONFIG_ERROR + "\n")


class ComposeRecoveryMountTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="waf-mount-check-")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.database = self.directory / "waf.db"
        self.database.write_bytes(b"synthetic database not read by metadata checks")
        self.ismount = mock.patch.object(checks.os.path, "ismount", return_value=True).start()
        self.addCleanup(mock.patch.stopall)
        self.statvfs = mock.patch.object(checks.os, "statvfs",
                                        return_value=SimpleNamespace(f_flag=0)).start()

    def test_valid_files_and_unrelated_backup_are_never_opened_or_changed(self):
        for name in checks.DATABASE_FILES[1:]:
            (self.directory / name).write_bytes(b"synthetic sidecar")
        (self.directory / "backups").mkdir()
        (self.directory / "old.db").symlink_to(self.directory / "missing")
        before = {path.name: os.lstat(path) for path in self.directory.iterdir()}
        with mock.patch("builtins.open", side_effect=AssertionError("must not read contents")), \
                mock.patch.object(checks.os, "chmod", side_effect=AssertionError("no chmod")), \
                mock.patch.object(checks.os, "chown", side_effect=AssertionError("no chown")):
            checks.validate_mount(self.directory)
        self.assertEqual(before, {path.name: os.lstat(path) for path in self.directory.iterdir()})

    def test_rejects_not_mounted_or_readonly_filesystem(self):
        self.ismount.return_value = False
        with self.assertRaises(ValueError):
            checks.validate_mount(self.directory)
        self.ismount.return_value = True
        self.statvfs.return_value = SimpleNamespace(f_flag=os.ST_RDONLY)
        with self.assertRaises(ValueError):
            checks.validate_mount(self.directory)

    def test_requires_main_database(self):
        self.database.unlink()
        with self.assertRaises(FileNotFoundError):
            checks.validate_mount(self.directory)

    def test_rejects_symlink_directory_even_when_mocked_as_mountpoint(self):
        link = self.directory / "mount-link"
        link.symlink_to(self.directory, target_is_directory=True)
        with self.assertRaises(ValueError):
            checks.validate_mount(link)

    def test_rejects_symlinks_hardlinks_directories_and_fifos_for_all_db_files(self):
        for name in checks.DATABASE_FILES:
            for kind in ("symlink", "hardlink", "directory", "fifo"):
                with self.subTest(name=name, kind=kind):
                    path = self.directory / name
                    if path.exists():
                        path.unlink()
                    if kind == "symlink":
                        path.symlink_to(self.directory / "missing")
                    elif kind == "hardlink":
                        target = self.directory / "hardlink-target"
                        target.write_bytes(b"synthetic")
                        os.link(target, path)
                    elif kind == "directory":
                        path.mkdir()
                    else:
                        os.mkfifo(path)
                    try:
                        with self.assertRaises(ValueError):
                            checks.validate_mount(self.directory)
                    finally:
                        path.rmdir() if kind == "directory" else path.unlink()
                        if name == "waf.db":
                            self.database.write_bytes(b"synthetic")

    def test_cli_mount_errors_are_sanitized_and_no_output_on_success(self):
        for error, expected in ((None, 0), (OSError(SYNTHETIC_SECRET), 1)):
            with self.subTest(expected=expected):
                output, errors = io.StringIO(), io.StringIO()
                with mock.patch.object(checks, "validate_mount", side_effect=error) as validate:
                    status = checks.main(["mount"], stdout=output, stderr=errors)
                validate.assert_called_once_with()
                self.assertEqual(status, expected)
                self.assertEqual(output.getvalue(), "")
                self.assertEqual(errors.getvalue(), "" if not expected else checks.MOUNT_ERROR + "\n")

    def test_invalid_cli_arguments_fail_without_running_checks(self):
        for arguments in ([], ["config"], ["config", "volume", "extra"],
                          ["mount", "extra"], [SYNTHETIC_SECRET]):
            with self.subTest(arguments_length=len(arguments)):
                output, errors = io.StringIO(), io.StringIO()
                with mock.patch.object(checks, "validate_mount") as validate:
                    status = checks.main(arguments, stdout=output, stderr=errors)
                validate.assert_not_called()
                self.assertEqual(status, 1)
                self.assertEqual(output.getvalue(), "")
                self.assertEqual(errors.getvalue(), checks.USAGE_ERROR + "\n")


if __name__ == "__main__":
    unittest.main()
