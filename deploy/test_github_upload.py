"""All Git writes and pushes use disposable local repositories, never GitHub."""
from argparse import Namespace
from contextlib import redirect_stdout, redirect_stderr
import io
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from deploy import github_upload as uploader


class SafetyTests(unittest.TestCase):
    def test_official_https_or_ssh_without_embedded_tokens(self):
        for url in ("https://github.com/example/waf.git", "git@github.com:example/waf.git", "ssh://git@github.com/example/waf.git"):
            self.assertEqual(uploader.github_repository(url), "example/waf")
        for url in ("https://token@github.com/example/waf.git", "https://git:token@github.com/example/waf.git",
                    "https://github.com.invalid/example/waf", "file:///tmp/repo", "http://github.com/example/waf",
                    "ssh://git@other.example/example/waf", "https://github.com/example/waf?token=example"):
            with self.subTest(url=url), self.assertRaises(uploader.UploadError):
                uploader.github_repository(url)

    def test_private_files_and_generated_files(self):
        for path in (".env", ".env.production", "app/runtime.env", ".local-deploy/config.json", "backup.db",
                     "secret.pem", "logs/access.log", "archive.zip", ".ssh/config"):
            self.assertTrue(uploader.private_path(path), path)
        for path in (".env.example", ".env.production.example", "backend/app/main.py"):
            self.assertFalse(uploader.private_path(path), path)
        self.assertTrue(uploader.generated("backend/package.egg-info/PKG-INFO"))
        self.assertTrue(uploader.generated("frontend/node_modules/package/index.js"))

    def test_known_token_is_rejected_without_printing_it(self):
        for token in ("ghp_" + "a" * 36, "wafsvc_" + "0" * 32 + "_" + "x" * 43):
            with self.assertRaises(uploader.UploadError) as caught:
                uploader.inspect_content(token.encode(), "synthetic.txt")
            self.assertNotIn(token, str(caught.exception))
        uploader.inspect_content(b"sk-example-only", "example.txt")


class RepositoryTests(unittest.TestCase):
    def raw(self, cwd, *args):
        return subprocess.check_output(["git", "-C", str(cwd), *args], stderr=subprocess.DEVNULL).decode().strip()

    def identity(self, root):
        for key, value in (("user.name", "Upload script test"), ("user.email", "test@example.invalid"),
                           ("commit.gpgsign", "false"), ("tag.gpgsign", "false"), ("core.hooksPath", "/dev/null")):
            self.raw(root, "config", key, value)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "work"
        self.root.mkdir()
        self.raw(self.root, "init", "-b", "main")
        self.identity(self.root)
        (self.root / "README.md").write_text("Synthetic initial commit\n")
        self.raw(self.root, "add", "README.md")
        self.raw(self.root, "commit", "-m", "Initial")
        self.remote = self.base / "remote.git"
        self.raw(self.base, "init", "--bare", str(self.remote))
        self.raw(self.root, "remote", "add", "origin", str(self.remote))
        self.raw(self.root, "push", "origin", "main")
        self.git = uploader.Git(self.root)
        self.head = self.git.text("rev-parse", "HEAD")
        self.args = Namespace(remote="origin", tag=None, message="Synthetic change")

    def run_upload(self, branch="main", snapshot=None):
        with redirect_stdout(io.StringIO()):
            return uploader.upload(self.git, self.args, branch, self.head, self.git.changes()[0], snapshot)

    def test_commit_and_push_only_selected_files(self):
        (self.root / "app.py").write_text("print('synthetic')\n")
        (self.root / ".env").write_text("SYNTHETIC_ONLY=placeholder\n")
        package = self.root / "backend/example.egg-info"
        package.mkdir(parents=True)
        (package / "PKG-INFO").write_text("generated")
        self.run_upload()
        self.assertNotEqual(self.git.text("rev-parse", "HEAD"), self.head)
        files = self.git.paths("ls-tree", "--name-only", "-r", "-z", "HEAD")
        self.assertEqual(set(files), {"README.md", "app.py"})
        self.assertTrue((self.root / ".env").exists())
        remote_head = self.raw(self.remote, "rev-parse", "refs/heads/main")
        self.assertEqual(remote_head, self.git.text("rev-parse", "HEAD"))
        self.assertEqual(self.raw(self.remote, "tag"), "")

    def test_branch_and_tag_explicit_atomic_push_leaves_main_alone(self):
        (self.root / "app.py").write_text("# synthetic\n")
        self.args.tag = "v0.3.0-test"
        self.run_upload("candidate")
        self.assertEqual(self.raw(self.remote, "rev-parse", "refs/heads/main"), self.head)
        self.assertEqual(self.raw(self.remote, "rev-parse", "refs/heads/candidate"), self.git.text("rev-parse", "HEAD"))
        self.assertEqual(self.raw(self.remote, "rev-parse", "refs/tags/v0.3.0-test^{commit}"), self.git.text("rev-parse", "HEAD"))
        # Reusing the same tag/commit is harmless; no second commit is made.
        self.head = self.git.text("rev-parse", "HEAD")
        self.run_upload("candidate")
        self.assertEqual(self.git.text("rev-parse", "HEAD"), self.head)

    def test_remote_advances_refuse_before_local_commit(self):
        peer = self.base / "peer"
        self.raw(self.base, "clone", "--branch", "main", str(self.remote), str(peer))
        self.identity(peer)
        (peer / "remote.txt").write_text("another user's synthetic change")
        self.raw(peer, "add", ".")
        self.raw(peer, "commit", "-m", "Remote change")
        self.raw(peer, "push", "origin", "main")
        (self.root / "local.txt").write_text("local change")
        with self.assertRaisesRegex(uploader.UploadError, "원격 브랜치"):
            self.run_upload()
        self.assertEqual(self.git.text("rev-parse", "HEAD"), self.head)
        self.git.clean_index()

    def test_existing_staging_preserved(self):
        (self.root / "staged.txt").write_text("existing user's stage")
        self.raw(self.root, "add", "staged.txt")
        index = (self.root / ".git/index").read_bytes()
        with self.assertRaisesRegex(uploader.UploadError, "스테이징"):
            self.git.clean_index()
        self.assertEqual(index, (self.root / ".git/index").read_bytes())

    def test_deleted_secret_still_detected_in_outgoing_history(self):
        (self.root / ".env").write_text("SYNTHETIC_ONLY=placeholder")
        self.raw(self.root, "add", ".env")
        self.raw(self.root, "commit", "-m", "Synthetic unsafe history")
        self.raw(self.root, "rm", ".env")
        self.raw(self.root, "commit", "-m", "Delete synthetic file")
        self.head = self.git.text("rev-parse", "HEAD")
        with self.assertRaisesRegex(uploader.UploadError, "비공개"):
            self.run_upload()
        self.assertNotEqual(self.raw(self.remote, "rev-parse", "main"), self.head)

    def test_moved_git_directory_auto_detected_without_touching_dot_git(self):
        target = self.root / ".local-deploy/canonical-v5-reviewed.git"
        target.parent.mkdir()
        (self.root / ".git").rename(target)
        (self.root / ".git").mkdir()
        git = uploader.Git(self.root)
        self.assertTrue(git.alternate)
        self.assertEqual(git.text("rev-parse", "HEAD"), self.head)
        self.assertEqual(list((self.root / ".git").iterdir()), [])

    def test_same_path_content_change_after_confirmation_refused(self):
        (self.root / "README.md").write_text("reviewed content")
        snapshot = self.git.snapshot(self.git.changes()[0])
        (self.root / "README.md").write_text("unreviewed content")
        with self.assertRaisesRegex(uploader.UploadError, "상태가 바뀌"):
            self.run_upload(snapshot=snapshot)
        self.assertEqual(self.git.text("rev-parse", "HEAD"), self.head)

    def test_existing_tag_is_not_moved(self):
        self.raw(self.root, "tag", "v0.3.0-test")
        self.raw(self.root, "push", "origin", "refs/tags/v0.3.0-test")
        (self.root / "app.py").write_text("# synthetic")
        self.args.tag = "v0.3.0-test"
        with self.assertRaisesRegex(uploader.UploadError, "원격 태그"):
            self.run_upload()
        self.assertEqual(self.raw(self.remote, "rev-parse", "main"), self.head)
        self.assertEqual(self.raw(self.remote, "rev-parse", "refs/tags/v0.3.0-test"), self.head)

    def test_check_mode_no_commit_fetch_or_push(self):
        (self.root / "app.py").write_text("# synthetic")
        with patch.object(uploader, "ROOT", self.root), patch.object(uploader, "validate_remote", return_value="example/waf"), \
             patch.object(uploader, "upload") as upload, redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(uploader.main(["--check"]), 0)
        upload.assert_not_called()
        self.assertEqual(self.git.text("rev-parse", "HEAD"), self.head)
        self.assertFalse((self.root / ".git/FETCH_HEAD").exists())
        self.git.clean_index()


if __name__ == "__main__":
    unittest.main()
