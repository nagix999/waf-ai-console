"""Operator-run GitHub upload; no network access in --check mode."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
MAX_BLOB = 100 * 1024 * 1024
GENERATED = {"node_modules", ".venv", "__pycache__", ".pytest_cache", "dist", "test-results", "playwright-report"}
PRIVATE_DIRS = {".local-deploy", ".agents", ".codex", ".ssh", ".aws", ".git", "secrets"}
SECRET_PATTERNS = (
    rb"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{24,}",
    rb"\bgh[pousr]_[A-Za-z0-9]{30,}",
    rb"\bgithub_pat_[A-Za-z0-9_]{40,}",
    rb"\bwafsvc_[0-9a-f]{32}_[A-Za-z0-9_-]{43}",
    rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----[\r\n]+[A-Za-z0-9+/=\r\n]{32,}",
)


class UploadError(Exception):
    pass


def require(condition, message):
    if not condition:
        raise UploadError(message)


def display(path):
    return json.dumps(path, ensure_ascii=False)


def generated(path):
    return any(part in GENERATED or part.endswith(".egg-info") for part in PurePosixPath(path).parts)


def private_path(path):
    parts = PurePosixPath(path).parts
    name = parts[-1].lower()
    if any(part.lower() in PRIVATE_DIRS for part in parts):
        return True
    if name.startswith(".env") or name.endswith(".env"):
        return name not in {".env.example", ".env.production.example"}
    return (name in {"id_rsa", "id_ed25519", "credentials"} or
            name.endswith((".db", ".db-wal", ".db-shm", ".sqlite", ".sqlite3", ".sqlite-wal", ".sqlite-shm",
                           ".bak", ".log", ".pem", ".key", ".p12", ".pfx", ".kdbx", ".tfstate",
                           ".zip", ".tar", ".tar.gz", ".tgz")))


def inspect_content(data, path):
    require(len(data) < MAX_BLOB, "100 MiB 이상 파일은 업로드하지 않습니다: " + display(path))
    require(not any(re.search(pattern, data) for pattern in SECRET_PATTERNS),
            "인증 키 또는 개인 키로 보이는 내용이 있습니다. 값을 출력하지 않습니다: " + display(path))


class Git:
    def __init__(self, root, git_dir=None):
        self.root = Path(root).resolve()
        self.env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")
                    or key in {"GIT_SSH", "GIT_SSH_COMMAND", "GIT_ASKPASS"}}
        self.env.update(GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0")
        self.prefix = ["git", "--literal-pathspecs", "-C", str(self.root)]
        self.alternate = False
        if git_dir:
            self.prefix += ["--git-dir=" + str(Path(git_dir).resolve()), "--work-tree=" + str(self.root)]
        else:
            result = self.call("rev-parse", "--show-toplevel", ok=(0, 128))
            if result.returncode or Path(result.stdout.decode().strip()).resolve() != self.root:
                fallback = self.root / ".local-deploy/canonical-v5-reviewed.git"
                require((fallback / "HEAD").is_file(), "Git 저장소를 찾지 못했습니다. --git-dir 경로를 지정하세요.")
                self.prefix += ["--git-dir=" + str(fallback), "--work-tree=" + str(self.root)]
                self.alternate = True
        self.call("rev-parse", "--verify", "HEAD^{commit}")

    def call(self, *args, data=None, ok=(0,)):
        try:
            result = subprocess.run([*self.prefix, *args], input=data, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, env=self.env, timeout=300)
        except (OSError, subprocess.TimeoutExpired):
            raise UploadError("Git 실행 실패 또는 시간 초과. 인증·연결·Git 설치를 확인하세요.") from None
        require(result.returncode in ok, "Git " + args[0] + " 실패. 인증·권한·원격 변경 여부를 확인하세요. 원문 오류는 비밀 값 보호를 위해 숨깁니다.")
        return result

    def text(self, *args):
        return self.call(*args).stdout.decode().strip()

    def paths(self, *args):
        return [value.decode() for value in self.call(*args).stdout.split(b"\0") if value]

    def clean_index(self):
        require(self.call("diff", "--cached", "--quiet", ok=(0, 1)).returncode == 0,
                "이미 스테이징한 변경이 있습니다. 기존 작업 보호를 위해 중단합니다. 직접 커밋한 뒤 다시 실행하세요.")
        for state in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply"):
            path = Path(self.text("rev-parse", "--git-path", state))
            require(not (path if path.is_absolute() else self.root / path).exists(), "병합·리베이스 작업을 먼저 마무리하세요.")

    def changes(self):
        tracked = self.paths("diff", "--name-only", "--no-renames", "-z", "HEAD", "--")
        untracked = self.paths("ls-files", "--others", "--exclude-standard", "-z")
        skipped = sorted(path for path in untracked if generated(path) or private_path(path))
        selected = sorted(set(tracked + [path for path in untracked if path not in skipped]))
        for path in selected:
            require(not private_path(path) and not generated(path), "추적 중인 비공개·생성 파일이 있습니다. 수동으로 검토하세요: " + display(path))
            full = self.root / path
            require(not full.is_symlink(), "변경된 symlink는 자동 업로드하지 않습니다: " + display(path))
            if full.exists():
                require(full.is_file(), "서브모듈·디렉터리는 수동으로 검토하세요: " + display(path))
                require(full.stat().st_size < MAX_BLOB, "100 MiB 이상 파일입니다: " + display(path))
                inspect_content(full.read_bytes(), path)
        return selected, skipped

    def snapshot(self, paths):
        result = {}
        for path in paths:
            full = self.root / path
            require(not full.is_symlink(), "변경된 symlink는 업로드하지 않습니다.")
            result[path] = (full.stat().st_mode, hashlib.sha256(full.read_bytes()).hexdigest()) if full.exists() else None
        return result

    def scan_trees(self, revisions):
        """Inspect blobs directly, without checking out or following repository symlinks."""
        blobs = {}
        for revision in revisions:
            for item in self.call("ls-tree", "-r", "-z", revision).stdout.split(b"\0"):
                if not item:
                    continue
                metadata, raw_path = item.split(b"\t", 1)
                mode, kind, oid = metadata.decode().split()
                path = raw_path.decode()
                require(not private_path(path) and not generated(path),
                        "커밋에 비공개·생성 파일이 있습니다: " + display(path))
                require(kind == "blob", "서브모듈이 있는 커밋은 수동으로 검토하세요: " + display(path))
                blobs.setdefault(oid, path)
        process = subprocess.Popen([*self.prefix, "cat-file", "--batch"], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=self.env)
        try:
            for oid, path in blobs.items():
                process.stdin.write(oid.encode() + b"\n")
                process.stdin.flush()
                header = process.stdout.readline().split()
                require(len(header) == 3 and header[1] == b"blob", "Git 객체를 읽지 못했습니다.")
                size = int(header[2])
                require(size < MAX_BLOB, "100 MiB 이상 파일이 커밋에 있습니다: " + display(path))
                inspect_content(process.stdout.read(size), path)
                require(process.stdout.read(1) == b"\n", "Git 객체 응답이 올바르지 않습니다.")
        finally:
            process.stdin.close()
            process.stdout.close()
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=10)


def github_repository(url):
    if url.startswith("git@github.com:"):
        path = url[len("git@github.com:"):]
    else:
        parsed = urlsplit(url)
        require(parsed.scheme in {"https", "ssh"} and parsed.hostname == "github.com"
                and not parsed.password and not parsed.query and not parsed.fragment
                and parsed.port in (None, 22, 443)
                and parsed.username in ((None, "git") if parsed.scheme == "ssh" else (None,)),
                "공식 GitHub HTTPS/SSH 주소만 사용하며 URL에 토큰을 넣을 수 없습니다.")
        path = parsed.path.lstrip("/")
    require(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?", path), "GitHub 저장소 주소를 확인하세요.")
    return path[:-4] if path.endswith(".git") else path


def validate_remote(git, remote):
    require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", remote), "remote 이름이 올바르지 않습니다.")
    fetch = git.text("remote", "get-url", "--all", remote).splitlines()
    push = git.text("remote", "get-url", "--push", "--all", remote).splitlines()
    require(len(fetch) == len(push) == 1, "remote의 조회·업로드 주소는 각각 하나여야 합니다.")
    repository = github_repository(fetch[0])
    require(github_repository(push[0]) == repository, "조회와 업로드 저장소가 다릅니다.")
    mirror = git.call("config", "--bool", "--get", f"remote.{remote}.mirror", ok=(0, 1)).stdout.strip()
    require(mirror != b"true", "mirror remote는 사용할 수 없습니다.")
    return repository


def upload(git, args, branch, original_head, selected, snapshot=None):
    snapshot = git.snapshot(selected) if snapshot is None else snapshot
    # Explicit fetch destination: never update local branches or import release tags.
    print("원격 브랜치 확인 중…", flush=True)
    git.call("fetch", "--no-tags", "--prune", args.remote,
             f"+refs/heads/*:refs/remotes/{args.remote}/*")
    remote_ref = f"refs/remotes/{args.remote}/{branch}"
    exists = git.call("show-ref", "--verify", "--quiet", remote_ref, ok=(0, 1)).returncode == 0
    if exists:
        require(git.call("merge-base", "--is-ancestor", remote_ref, "HEAD", ok=(0, 1)).returncode == 0,
                "원격 브랜치에 로컬에 없는 변경이 있습니다. 자동 병합·강제 업로드하지 않습니다. 먼저 변경 내용을 통합하세요.")
    git.clean_index()
    require(git.text("rev-parse", "HEAD") == original_head and git.changes()[0] == selected
            and git.snapshot(selected) == snapshot,
            "확인 이후 Git 상태가 바뀌었습니다. 다시 실행하세요.")
    # Includes intermediate commits; deleting a secret in a later commit does not remove it from history.
    outgoing = git.text("rev-list", "HEAD", "--not", f"--remotes={args.remote}").splitlines()
    git.scan_trees(outgoing or ["HEAD"])
    if selected:
        print("확인한 변경을 커밋합니다.", flush=True)
        git.call("add", "--pathspec-from-file=-", "--pathspec-file-nul",
                 data=b"\0".join(path.encode() for path in selected) + b"\0")
        # Scan the index, not just the worktree, before making a commit.
        tree = git.text("write-tree")
        git.scan_trees([tree])
        git.call("commit", "-m", args.message)
        require(git.text("rev-parse", "HEAD^{tree}") == tree, "Git hook이 커밋 내용을 변경했습니다. 로컬 커밋을 검토한 뒤 다시 실행하세요.")
    commit = git.text("rev-parse", "HEAD")
    git.scan_trees([commit])
    require(not git.paths("diff", "--name-only", "-z", "HEAD", "--"),
            "커밋 과정에서 작업 파일이 바뀌었습니다. 로컬 커밋을 검토한 후 다시 실행하세요.")
    refs = [f"{commit}:refs/heads/{branch}"]
    if args.tag:
        remote_tag = dict(line.split("\t", 1)[::-1] for line in git.text(
            "ls-remote", "--tags", args.remote, f"refs/tags/{args.tag}", f"refs/tags/{args.tag}^{{}}").splitlines())
        remote_commit = remote_tag.get(f"refs/tags/{args.tag}^{{}}", remote_tag.get(f"refs/tags/{args.tag}"))
        if remote_commit:
            require(remote_commit == commit, "원격 태그가 다른 커밋을 가리킵니다. 기존 태그를 변경하지 않습니다.")
        else:
            local_exists = git.call("show-ref", "--verify", "--quiet", f"refs/tags/{args.tag}", ok=(0, 1)).returncode == 0
            if local_exists:
                require(git.text("rev-parse", f"refs/tags/{args.tag}^{{commit}}") == commit,
                        "로컬 태그가 다른 커밋을 가리킵니다. 기존 태그를 변경하지 않습니다.")
            else:
                git.call("tag", "-a", args.tag, "-m", "Release " + args.tag, commit)
            refs.append(f"refs/tags/{args.tag}:refs/tags/{args.tag}")
    print("확인한 브랜치" + ("·태그" if args.tag else "") + " 업로드 중…", flush=True)
    git.call("push", "--atomic", "--no-force", "--no-follow-tags", args.remote, *refs)
    actual = git.text("ls-remote", "--heads", args.remote, f"refs/heads/{branch}").split()
    require(actual and actual[0] == commit, "업로드 후 원격 커밋을 확인하지 못했습니다. GitHub에서 상태를 확인하세요.")
    print(f"완료: {branch} / {commit[:12]}" + (f" / {args.tag}" if args.tag else ""))


def main(argv=None):
    parser = argparse.ArgumentParser(description="변경을 검토·커밋하고 현재 브랜치를 GitHub에 업로드합니다. 강제 push·자동 merge·Docker 배포는 하지 않습니다.")
    parser.add_argument("--check", action="store_true", help="파일·설정 검사만; 네트워크·Git 쓰기 없음")
    parser.add_argument("--message", "-m", help="새 변경을 커밋할 메시지; 변경이 없으면 생략 가능")
    parser.add_argument("--remote", default="origin", help="GitHub remote 이름 (기본 origin)")
    parser.add_argument("--branch", help="대상 원격 브랜치 (기본 현재 브랜치; checkout/merge는 하지 않음)")
    parser.add_argument("--tag", help="같은 커밋에 추가할 태그. 생략 시 태그 생성/업로드 없음")
    parser.add_argument("--git-dir", help="별도 Git 메타데이터 경로; 현재 canonical 저장소는 자동 탐색")
    parser.add_argument("--yes", action="store_true", help="변경 파일·저장소·대상을 이미 검토한 경우 확인 질문 생략")
    args = parser.parse_args(argv)
    try:
        git = Git(ROOT, args.git_dir)
        git.clean_index()
        current = git.text("symbolic-ref", "--quiet", "--short", "HEAD")
        branch = args.branch or current
        git.call("check-ref-format", "refs/heads/" + branch)
        if args.tag:
            require(not args.tag.startswith("-"), "태그 이름은 -로 시작할 수 없습니다.")
            git.call("check-ref-format", "refs/tags/" + args.tag)
        repository = validate_remote(git, args.remote)
        head = git.text("rev-parse", "HEAD")
        selected, skipped = git.changes()
        snapshot = git.snapshot(selected)
        git.scan_trees(["HEAD"])
        print(f"저장소: {repository}\n현재 브랜치: {current}\n업로드 대상: {branch}\n태그: {args.tag or '추가하지 않음'}")
        if git.alternate:
            print("별도 Git 메타데이터 사용: .local-deploy/canonical-v5-reviewed.git (기존 .git은 변경하지 않음)")
        print(f"커밋할 변경 {len(selected)}개:")
        for path in selected:
            print("  " + display(path))
        if skipped:
            print(f"비공개·생성 파일 {len(skipped)}개는 제외합니다. 기존 파일은 지우지 않습니다.")
        print("자동 검사는 알려진 키 형식·파일명만 확인합니다. 실제 운영 로그와 민감정보 포함 여부는 직접 검토하세요.")
        if args.check:
            print("로컬 검사 통과. 커밋·태그·fetch·push는 하지 않았습니다. 원격 최신 상태는 실제 실행에서 확인합니다.")
            return 0
        require(not selected or (args.message and args.message.strip()), "변경을 커밋하려면 --message '커밋 설명'을 지정하세요.")
        if branch != current:
            print("주의: 현재 브랜치의 전체 커밋을 다른 원격 브랜치에 반영합니다. PR이나 merge를 생성하는 동작이 아닙니다.")
        confirmation = repository + ":" + branch
        if not args.yes:
            require(sys.stdin.isatty(), "대화형 터미널에서 실행하거나 검토 후 --yes를 지정하세요.")
            if input("계속하려면 " + confirmation + " 입력: ").strip() != confirmation:
                print("취소했습니다. Git 상태와 원격 저장소는 변경하지 않았습니다.")
                return 0
        upload(git, args, branch, head, selected, snapshot)
        return 0
    except (UploadError, OSError, ValueError, UnicodeError, KeyboardInterrupt, subprocess.SubprocessError) as error:
        print("중단: " + (str(error) if isinstance(error, UploadError) else "검사 실패 또는 사용자 중단. 민감한 출력은 생략합니다."), file=sys.stderr)
        print("실제 실행 중 중단됐다면 커밋·태그·스테이징이 로컬에 남거나 원격 업로드가 완료됐을 수 있습니다. 자동 reset·태그 삭제·재업로드는 하지 않습니다.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
