"""One confirmation: publish explicit main ref, then deploy that commit locally."""
import argparse
from contextlib import nullcontext
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from deploy.automation.common import DeployError, operator_lock, private_write, secure_dir
from deploy.github_upload import Git, UploadError, display, require, upload, validate_remote
from deploy.redeploy import Redeploy, environment, ports


def export_commit(git, commit, directory):
    """Build only the published commit; never copy secrets or the changing worktree."""
    archive = directory / "source.tar"
    git.call("archive", "--format=tar", "--output=" + str(archive), commit)
    archive.chmod(0o600)
    target = secure_dir(directory / "source")
    with tarfile.open(archive, "r:") as stream:
        for member in stream:
            path = PurePosixPath(member.name)
            require(not path.is_absolute() and ".." not in path.parts and bool(path.parts), "Git 소스 경로가 올바르지 않습니다.")
            require(member.isdir() or member.isfile(), "링크·특수 파일이 있는 소스는 자동 배포하지 않습니다.")
            destination = target.joinpath(*path.parts)
            require(not destination.is_symlink(), "소스 경로가 symlink입니다.")
            if member.isdir():
                destination.mkdir(parents=True, exist_ok=True, mode=0o755)
            else:
                destination.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
                with stream.extractfile(member) as source, destination.open("xb") as output:
                    shutil.copyfileobj(source, output)
                destination.chmod(0o755 if member.mode & 0o111 else 0o644)
    for name in (".dockerignore", "backend/Dockerfile", "frontend/Dockerfile", "deploy/redeploy_db.py"):
        require((target / name).is_file(), "배포 커밋에 필수 파일이 없습니다: " + name)
    return target


class Release:
    def __init__(self, args):
        self.args = args
        self.git = None
        self.docker = Redeploy(args)
        self.directory = None
        self.commit = None
        self.phase = "사전 확인"
        self.push_started = False

    def prepare(self):
        self.git = Git(ROOT, self.args.git_dir)
        self.git.clean_index()
        self.branch = self.git.text("symbolic-ref", "--quiet", "--short", "HEAD")
        self.repository = validate_remote(self.git, self.args.remote)
        if self.args.tag:
            require(not self.args.tag.startswith("-"), "태그 이름이 올바르지 않습니다.")
            self.git.call("check-ref-format", "refs/tags/" + self.args.tag)
        self.head = self.git.text("rev-parse", "HEAD")
        self.selected, self.skipped = self.git.changes()
        self.snapshot = self.git.snapshot(self.selected)
        self.git.scan_trees(["HEAD"])
        self.docker.discover()
        self.original_model = deepcopy(self.docker.model)
        self.original_ids = {name: row["Id"] for name, row in self.docker.rows.items()}
        self.original_project = self.docker.project
        print(f"GitHub: {self.repository}\n현재 브랜치: {self.branch}\n반영 대상: main\n태그: {self.args.tag or '추가하지 않음'}")
        print(f"Docker: {self.docker.project} / {environment(self.docker.rows['api'])['WAF_AGENT_MODE']}")
        print("포트: " + json.dumps({name: ports(service) for name, service in self.docker.model["services"].items() if service.get("ports")}))
        print(f"커밋할 변경 {len(self.selected)}개 (생성물·비공개 파일 {len(self.skipped)}개 제외):")
        for name in self.selected:
            print("  " + display(name))
        if self.selected:
            print("커밋 설명: " + self.args.message)
        print("알려진 키 형식·비공개 경로를 검사했지만, 실제 운영 데이터나 모든 비밀 값을 판별할 수는 없습니다. 변경 목록을 확인하세요.")

    def record(self, phase):
        self.phase = phase
        private_write(self.directory / "state.json", {
            "phase": phase, "repository": self.repository, "target_branch": "main",
            "commit": self.commit, "docker_project": self.original_project,
            "docker_record": str(self.docker.directory) if self.docker.directory else None,
            "at": datetime.now(timezone.utc).isoformat(),
        })
        print(phase, flush=True)

    def execute(self):
        root = secure_dir(ROOT / ".local-deploy/release")
        self.directory = Path(tempfile.mkdtemp(prefix="run-", dir=root))
        self.record("GitHub main 반영 중")
        self.push_started = True
        self.commit = upload(self.git, self.args, "main", self.head, self.selected, self.snapshot)
        self.record("GitHub main 반영 완료 — 해당 커밋의 배포 소스 준비 중")
        source = export_commit(self.git, self.commit, self.directory)
        # Recheck after network/commit work; never silently switch Docker targets.
        self.docker.discover()
        require(self.docker.project == self.original_project and self.docker.model == self.original_model
                and {name: row["Id"] for name, row in self.docker.rows.items()} == self.original_ids,
                "GitHub 처리 중 Docker 대상 또는 설정이 바뀌었습니다. Docker 배포는 중단합니다.")
        self.record("Docker 재배포 중")
        self.docker.deploy(source_root=source)
        self.record("전체 완료")
        print(f"main 반영·Docker 재배포 완료: {self.commit[:12]}")
        print("실행 기록: " + str(self.directory))


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="변경 검토 → 커밋 → GitHub main fast-forward 반영 → 이 서버의 WAF 재배포. 원격 서버 SSH 배포나 Gateway 변경은 하지 않습니다.",
        epilog="기존 Gateway 연동: bash scripts/deploy.sh gateway {check,deploy,status,rollback}. 과거의 동일 명령도 호환 유지합니다.")
    parser.add_argument("--check", action="store_true", help="Git·Docker 사전 검사만; GitHub 연결/변경·서비스 중지 없음")
    parser.add_argument("--message", "-m", default="Deploy current changes to main", help="커밋 설명 (변경이 있을 때만 사용)")
    parser.add_argument("--yes", action="store_true", help="대상과 변경 파일을 검토한 경우 확인 질문 생략")
    parser.add_argument("--remote", default="origin", help="GitHub remote 이름")
    parser.add_argument("--tag", help="같은 커밋에 명시적으로 추가할 태그; 기본 추가하지 않음")
    parser.add_argument("--git-dir", help="Git 메타데이터 경로; canonical 저장소는 자동 탐색")
    parser.add_argument("--project", help="기존 WAF Compose 프로젝트 이름")
    parser.add_argument("--file", "-f", action="append", help="기존 Compose 파일; 여러 파일은 순서대로 반복")
    parser.add_argument("--env-file", help="기존 Compose env 경로; 값은 출력하지 않음")
    args = parser.parse_args(argv)
    operation = Release(args)
    try:
        require(bool(args.message.strip()), "커밋 설명은 빈 문자열일 수 없습니다.")
        # Shared with standalone redeploy: publishing and deployment are one local operation.
        if args.check:
            lock = nullcontext()
        else:
            directory = secure_dir(ROOT / ".local-deploy/redeploy")
            lock = operator_lock(directory / "operation.lock")
        with lock:
            operation.prepare()
            if args.check:
                print("Git·Docker 사전 검사 통과. 커밋·fetch·push·빌드·DB 이전은 하지 않았습니다. 원격 최신 상태와 권한은 실제 실행에서 확인합니다.")
                return 0
            print("main이 현재 브랜치에 포함된 경우만 반영합니다. 충돌·브랜치 보호 시 중단하며 강제 push하지 않습니다.")
            print("GitHub 반영 후 현재 서버의 WAF가 잠시 중단됩니다. DB 백업·사본 이전 시험 후 같은 커밋으로 재배포합니다.")
            print("재기동 후 분석 요청은 기존 모델을 호출할 수 있습니다. 이미지 빌드에는 패키지 다운로드가 필요할 수 있습니다.")
            if not args.yes:
                require(sys.stdin.isatty(), "대화형 터미널에서 실행하거나 대상 검토 후 --yes를 사용하세요.")
                confirmation = operation.repository + ":main"
                if input("계속하려면 " + confirmation + " 입력: ").strip() != confirmation:
                    print("취소했습니다. GitHub·컨테이너·DB는 변경하지 않았습니다.")
                    return 0
            operation.execute()
            return 0
    except (UploadError, DeployError, OSError, ValueError, KeyError, tarfile.TarError, KeyboardInterrupt, subprocess.SubprocessError) as error:
        message = str(error) if isinstance(error, (UploadError, DeployError)) else "검사 실패 또는 사용자 중단. 비밀 값 보호를 위해 상세 출력은 생략합니다."
        print("중단: " + message, file=sys.stderr)
        if operation.directory:
            # Retain the last known phase; failure can follow an uncertain remote push.
            print("실행 기록: " + str(operation.directory), file=sys.stderr)
        if operation.push_started:
            print("로컬 커밋·태그가 남거나 GitHub main이 이미 반영됐을 수 있습니다. 자동 reset·강제 push·원격 되돌리기를 하지 않습니다.", file=sys.stderr)
        if operation.docker.directory:
            print("Docker 마지막 단계: " + operation.docker.phase + "\nDocker 기록: " + str(operation.docker.directory), file=sys.stderr)
        if operation.docker.stopped:
            print("서비스 일부가 중지되거나 새 버전으로 실행 중일 수 있습니다. DB를 자동 덮어쓰지 않습니다. 복구 안내를 먼저 확인하세요.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
