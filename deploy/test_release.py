"""Offline orchestration tests. No GitHub requests or real Docker mutations."""
from argparse import Namespace
from contextlib import redirect_stdout, redirect_stderr
from copy import deepcopy
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from deploy import release, redeploy
from deploy.automation.common import DeployError
from deploy.github_upload import Git, UploadError
from deploy.test_redeploy import fixture


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.args = Namespace(project=None, file=None, env_file=None, git_dir=None,
                              remote="origin", tag=None, message="Synthetic release")

    def operation(self):
        operation = release.Release(self.args)
        operation.git = object()
        operation.head = "a" * 40
        operation.selected, operation.snapshot = [], {}
        operation.repository = "synthetic/waf"
        operation.docker.model, operation.docker.rows = fixture()
        operation.docker.project = "waf-test"
        operation.original_model = deepcopy(operation.docker.model)
        operation.original_ids = {name: row["Id"] for name, row in operation.docker.rows.items()}
        operation.original_project = operation.docker.project
        return operation

    def test_order_and_published_commit_build_context(self):
        operation = self.operation()
        events = []
        source = self.root / "immutable-source"
        def upload(*args):
            self.assertEqual(args[2], "main")
            events.append("push")
            return "b" * 40
        def export(git, commit, directory):
            self.assertEqual(commit, "b" * 40)
            events.append("archive")
            return source
        operation.docker.discover = lambda: events.append("docker-recheck")
        operation.docker.deploy = lambda source_root: events.append(("deploy", source_root))
        with patch.object(release, "ROOT", self.root), patch.object(release, "upload", upload), \
             patch.object(release, "export_commit", export), redirect_stdout(io.StringIO()):
            operation.execute()
        self.assertEqual(events, ["push", "archive", "docker-recheck", ("deploy", source)])
        state = json.loads((operation.directory / "state.json").read_text())
        self.assertEqual(state["phase"], "전체 완료")
        self.assertEqual(state["commit"], "b" * 40)

    def test_push_failure_never_builds_or_deploys(self):
        operation = self.operation()
        with patch.object(release, "ROOT", self.root), patch.object(release, "upload", side_effect=UploadError("synthetic rejection")), \
             patch.object(release, "export_commit") as export, patch.object(operation.docker, "deploy") as deploy, redirect_stdout(io.StringIO()):
            with self.assertRaises(UploadError):
                operation.execute()
        export.assert_not_called()
        deploy.assert_not_called()

    def test_changed_docker_target_after_push_refuses_deploy(self):
        operation = self.operation()
        def changed():
            operation.docker.rows["api"]["Id"] = "replacement-container"
        with patch.object(release, "ROOT", self.root), patch.object(release, "upload", return_value="b" * 40), \
             patch.object(release, "export_commit", return_value=self.root), \
             patch.object(operation.docker, "discover", changed), patch.object(operation.docker, "deploy") as deploy, redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(UploadError, "Docker 대상"):
                operation.execute()
        deploy.assert_not_called()
        self.assertEqual(operation.commit, "b" * 40)

    def test_deploy_failure_retains_published_commit_and_no_second_push(self):
        operation = self.operation()
        with patch.object(release, "ROOT", self.root), patch.object(release, "upload", return_value="b" * 40) as upload, \
             patch.object(release, "export_commit", return_value=self.root), patch.object(operation.docker, "discover"), \
             patch.object(operation.docker, "deploy", side_effect=DeployError("synthetic build failure")), redirect_stdout(io.StringIO()):
            with self.assertRaises(DeployError):
                operation.execute()
        upload.assert_called_once()
        state = json.loads((operation.directory / "state.json").read_text())
        self.assertEqual(state["commit"], "b" * 40)
        self.assertEqual(state["phase"], "Docker 재배포 중")

    def test_check_mode_does_not_create_lock_or_mutate(self):
        with patch.object(release, "ROOT", self.root), patch.object(release.Release, "prepare") as prepare, \
             patch.object(release.Release, "execute") as execute, redirect_stdout(io.StringIO()):
            self.assertEqual(release.main(["--check"]), 0)
        prepare.assert_called_once()
        execute.assert_not_called()
        self.assertFalse((self.root / ".local-deploy").exists())

    def test_preflight_failure_stops_before_execute(self):
        with patch.object(release, "ROOT", self.root), patch.object(release.Release, "prepare", side_effect=DeployError("missing env")), \
             patch.object(release.Release, "execute") as execute, redirect_stderr(io.StringIO()):
            self.assertEqual(release.main(["--yes"]), 1)
        execute.assert_not_called()

    def test_default_target_main_and_single_confirmation(self):
        def prepare(operation):
            operation.repository = "synthetic/waf"
        with patch.object(release, "ROOT", self.root), patch.object(release.Release, "prepare", prepare), \
             patch.object(release.Release, "execute") as execute, patch("sys.stdin.isatty", return_value=True), \
             patch("builtins.input", return_value="synthetic/waf:main") as confirmation, redirect_stdout(io.StringIO()):
            self.assertEqual(release.main([]), 0)
        confirmation.assert_called_once()
        execute.assert_called_once()

    def test_cancel_never_uploads_or_deploys(self):
        def prepare(operation):
            operation.repository = "synthetic/waf"
        with patch.object(release, "ROOT", self.root), patch.object(release.Release, "prepare", prepare), \
             patch.object(release.Release, "execute") as execute, patch("sys.stdin.isatty", return_value=True), \
             patch("builtins.input", return_value="no"), redirect_stdout(io.StringIO()):
            self.assertEqual(release.main([]), 0)
        execute.assert_not_called()

    def test_docker_builds_from_explicit_snapshot(self):
        model, _ = fixture()
        result = redeploy.target_model(model, "test", "stamp", self.root / "commit-source")
        for service in result["services"].values():
            self.assertEqual(service["build"]["context"], str(self.root / "commit-source"))


class ArchiveTests(unittest.TestCase):
    def test_archive_uses_commit_not_dirty_worktree_or_ignored_env(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            repo = base / "repo"
            repo.mkdir()
            def raw(*args):
                return subprocess.check_output(["git", "-C", str(repo), *args], stderr=subprocess.DEVNULL).decode().strip()
            raw("init", "-b", "main")
            raw("config", "user.name", "Synthetic test")
            raw("config", "user.email", "test@example.invalid")
            raw("config", "commit.gpgsign", "false")
            raw("config", "core.hooksPath", "/dev/null")
            for name in (".dockerignore", "backend/Dockerfile", "frontend/Dockerfile", "deploy/redeploy_db.py"):
                target = repo / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("committed synthetic content")
            raw("add", ".")
            raw("commit", "-m", "Synthetic sources")
            git = Git(repo)
            commit = git.text("rev-parse", "HEAD")
            (repo / "backend/Dockerfile").write_text("unpublished modification")
            (repo / ".env").write_text("SYNTHETIC_ONLY=not_for_build")
            output = base / "output"
            output.mkdir(mode=0o700)
            source = release.export_commit(git, commit, output)
            self.assertEqual((source / "backend/Dockerfile").read_text(), "committed synthetic content")
            self.assertFalse((source / ".env").exists())
            self.assertEqual((source / "backend/Dockerfile").stat().st_mode & 0o777, 0o644)

    def test_archive_refuses_escape_and_symlink(self):
        for name, kind in (("../escape", tarfile.REGTYPE), ("link", tarfile.SYMTYPE)):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temp:
                target = Path(temp)
                class FakeGit:
                    def call(self, *args):
                        with tarfile.open(target / "source.tar", "w") as stream:
                            member = tarfile.TarInfo(name)
                            member.type = kind
                            member.linkname = "/tmp/does-not-exist"
                            stream.addfile(member, io.BytesIO())
                with self.assertRaises(UploadError):
                    release.export_commit(FakeGit(), "synthetic", target)


class DispatchTests(unittest.TestCase):
    def test_new_default_help_and_legacy_gateway_commands(self):
        script = Path(__file__).resolve().parent.parent / "scripts/deploy.sh"
        checks = [(["--help"], "GitHub main"), (["gateway", "--help"], "팀 원본 파일"),
                  (["deploy", "--help"], "팀 원본 파일")]
        for args, expected in checks:
            with self.subTest(args=args):
                result = subprocess.run(["sh", str(script), *args], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(expected, result.stdout)


if __name__ == "__main__":
    unittest.main()
