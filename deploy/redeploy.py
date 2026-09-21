"""Redeploy an existing, single-host WAF Compose stack without touching gateways."""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from deploy.automation.common import (DeployError, Runner, compose_model, compose_text,
                                     secure_dir, private_write)

BACKENDS = ("api", "worker", "model-tester")
DB_URL = "sqlite+pysqlite:////data/waf.db"


def require(condition, message):
    if not condition:
        raise DeployError(message)


def environment(row):
    return dict(item.split("=", 1) for item in row["Config"]["Env"])


def ports(service):
    result = {}
    for port in service.get("ports", []):
        require(isinstance(port, dict) and port.get("published"), "고정된 공개 포트만 지원합니다.")
        key = str(port["target"]) + "/" + port.get("protocol", "tcp")
        result.setdefault(key, []).append({"HostIp": port.get("host_ip", ""), "HostPort": str(port["published"])})
    return result


def validate(model, rows):
    """Fail before mutation if the supplied Compose is not the live deployment."""
    services = model.get("services", {})
    require(set(services) == set(rows), "Compose와 실행 중인 서비스가 다릅니다. 기존 배포 파일을 지정하세요.")
    require(set(rows) in (set(BACKENDS) | {"frontend"}, set(BACKENDS) | {"waf-web"}),
            "API·두 worker·웹으로 구성된 WAF 전용 프로젝트만 지원합니다.")
    mounts = []
    for role, row in rows.items():
        service = services[role]
        require(row["State"]["Running"] and not row["State"].get("Paused"), "모든 기존 WAF 서비스가 실행 중이어야 합니다.")
        require(service.get("image") == row["Config"]["Image"], "실행 이미지와 Compose가 다릅니다.")
        require(service.get("command", row["Config"]["Cmd"]) == row["Config"]["Cmd"], "기존 실행 명령이 달라졌습니다.")
        require(service.get("entrypoint", row["Config"]["Entrypoint"]) == row["Config"]["Entrypoint"], "기존 entrypoint가 달라졌습니다.")
        current = environment(row)
        desired = service.get("environment", {})
        require(all(current.get(key) == str(value) for key, value in desired.items()),
                "현재 환경변수와 Compose가 다릅니다. 기존 env 파일을 지정하세요. 값은 출력하지 않습니다.")
        require(all(desired.get(key) == value for key, value in current.items() if key.startswith("WAF_")),
                "WAF 설정 누락 또는 변경을 감지했습니다. 기존 설정을 확인하세요.")
        require(ports(service) == (row["HostConfig"].get("PortBindings") or {}), "공개 포트가 기존 배포와 다릅니다.")
        expected_mounts = []
        for mount in service.get("volumes", []):
            require(isinstance(mount, dict) and mount.get("type") in ("bind", "volume"), "지원하지 않는 저장소 설정입니다.")
            source = mount["source"]
            if mount["type"] == "volume":
                source = model["volumes"][source]["name"]
            expected_mounts.append((mount["type"], source, mount["target"], not mount.get("read_only", False)))
        actual_mounts = [(item["Type"], item.get("Name") if item["Type"] == "volume" else item["Source"],
                          item["Destination"], item["RW"]) for item in row["Mounts"]]
        require(sorted(expected_mounts) == sorted(actual_mounts), "볼륨 또는 bind mount가 기존 배포와 다릅니다.")
        expected_networks = {model["networks"][name]["name"] for name in service.get("networks", {})}
        require(expected_networks == set(row["NetworkSettings"]["Networks"]), "기존 네트워크와 다릅니다.")
        if role in BACKENDS:
            require(current.get("WAF_DATABASE_URL") == DB_URL, "/data/waf.db SQLite 배포만 지원합니다.")
            data = [item for item in row["Mounts"] if item["Destination"] == "/data"]
            require(len(data) == 1 and data[0]["Type"] == "volume" and data[0]["RW"], "기존 /data 영속 볼륨을 확인할 수 없습니다.")
            mounts.append(data[0]["Name"])
    require(len(set(mounts)) == 1, "API와 worker의 DB 볼륨이 다릅니다.")
    for key in ("WAF_AGENT_MODE", "WAF_DATA_ENCRYPTION_KEY", "WAF_ENCRYPTION_KEY_VERSION"):
        require(len({environment(rows[role]).get(key) for role in BACKENDS}) == 1, "API와 worker의 모드 또는 암호화 설정이 다릅니다.")
    return mounts[0]


def target_model(model, project, stamp):
    result = deepcopy(model)
    result["name"] = project
    for role, service in result["services"].items():
        backend = role in BACKENDS
        service["image"] = f"{project}-{'backend' if backend else 'web'}:redeploy-{stamp}"
        service["build"] = {"context": str(ROOT), "dockerfile": "backend/Dockerfile" if backend else "frontend/Dockerfile"}
        service["pull_policy"] = "never"
    # Reuse resources by exact name. Missing resources must not create a fresh DB/network.
    for kind in ("volumes", "networks"):
        result[kind] = {key: {"name": value["name"], "external": True}
                        for key, value in model.get(kind, {}).items()}
    return result


class Redeploy:
    def __init__(self, args):
        self.args = args
        self.runner = Runner()
        self.directory = None
        self.stopped = False
        self.phase = "사전 확인"

    def run(self, command, message="명령 실행 실패", **kwargs):
        return self.runner.run(command, code=message, **kwargs)

    def inspect(self, ids):
        return json.loads(self.run(["docker", "inspect", *ids]))

    def discover(self):
        self.run(["docker", "compose", "version"], "Docker Compose v2가 필요합니다.")
        host = self.runner.env.get("DOCKER_HOST") if not self.runner.env.get("DOCKER_CONTEXT") else None
        if not host:
            host = json.loads(self.run(["docker", "context", "inspect", "--format", "{{json .Endpoints.docker.Host}}"],
                                      "Docker context를 확인하지 못했습니다."))
        require(host.startswith("unix://"), "로컬 Linux Docker만 지원합니다. 원격 Docker 대상은 허용하지 않습니다.")
        ids = self.run(["docker", "ps", "-aq", "--filter", "label=com.docker.compose.project"],
                       "Docker에 접근하지 못했습니다. Docker 실행 여부와 현재 계정의 접근 권한을 확인하세요.").split()
        require(ids, "기존 Compose 컨테이너가 없습니다. 이 스크립트는 신규 설치용이 아닙니다.")
        self.all_rows = self.inspect(ids)
        candidates = []
        for row in self.all_rows:
            labels = row["Config"]["Labels"]
            if labels.get("com.docker.compose.service") != "api" or labels.get("com.docker.compose.oneoff", "false").lower() == "true":
                continue
            project = labels["com.docker.compose.project"]
            directory = Path(labels.get("com.docker.compose.project.working_dir", "/"))
            matches = project == self.args.project if self.args.project else (ROOT == directory or ROOT in directory.parents)
            if matches:
                candidates.append(row)
        require(len(candidates) == 1, "WAF 프로젝트를 하나로 특정하지 못했습니다. --project 이름을 지정하세요.")
        api = candidates[0]
        labels = api["Config"]["Labels"]
        self.project = labels["com.docker.compose.project"]
        selected = [row for row in self.all_rows if row["Config"]["Labels"].get("com.docker.compose.project") == self.project
                    and row["Config"]["Labels"].get("com.docker.compose.oneoff", "false").lower() != "true"]
        self.rows = {row["Config"]["Labels"]["com.docker.compose.service"]: row for row in selected}
        require(len(selected) == len(self.rows), "서비스별 컨테이너가 여러 개입니다. 이 스크립트는 각 서비스 1개 배포용입니다.")
        files = self.args.file or labels.get("com.docker.compose.project.config_files", "").split(",")
        require(all(path and Path(path).is_file() for path in files), "기존 Compose 파일이 없습니다. --file 경로를 지정하세요.")
        command = ["docker", "compose", "-p", self.project]
        env_file = self.args.env_file or labels.get("com.docker.compose.project.environment_file")
        if env_file:
            for path in env_file.split(","):
                require(Path(path).is_file(), "기존 env 파일이 없습니다.")
                command += ["--env-file", str(Path(path).resolve())]
        for path in files:
            command += ["-f", str(Path(path).resolve())]
        self.model = compose_model(json.loads(self.run(command + ["config", "--format", "json"],
             "Compose 해석 실패. --file / --env-file을 확인하세요. 비밀 값 보호를 위해 원문 오류는 숨깁니다.")))
        self.volume = validate(self.model, self.rows)
        # Check every running container, including non-Compose consumers, before migration.
        live_ids = self.run(["docker", "ps", "-q"]).split()
        for row in self.inspect(live_ids) if live_ids else []:
            if any(mount.get("Name") == self.volume for mount in row["Mounts"]):
                require(row["Id"] in {value["Id"] for value in self.rows.values()}, "다른 컨테이너가 같은 DB 볼륨을 사용 중입니다.")
        self.web = "frontend" if "frontend" in self.rows else "waf-web"
        self.db_code = (ROOT / "deploy/redeploy_db.py").read_text()
        self.idle()

    def idle(self):
        self.run(["docker", "exec", self.rows["api"]["Id"], "python", "-c", self.db_code, "idle", "/data/waf.db"],
                 "미완료 분석·모델 검증이 있거나 DB를 확인하지 못했습니다. 작업 종료 후 다시 실행하세요.")

    def compose(self, *args, **kwargs):
        return self.run(["docker", "compose", "--env-file", "/dev/null", "-p", self.project,
                         "-f", str(self.directory / "compose.json"), *args], **kwargs)

    def helper(self, image, command, *, live=False, migration=False):
        args = ["docker", "run", "--rm", "--pull", "never", "--network", "none",
                "--mount", f"type=bind,source={self.directory},target=/backup"]
        if live:
            args += ["--mount", f"type=volume,source={self.volume},target=/data"]
        if migration:
            args += ["-e", "WAF_DATABASE_URL=" + (DB_URL if live else "sqlite+pysqlite:////backup/rehearsal.db")]
        return self.run([*args, "--entrypoint", command[0], image, *command[1:]],
                        "DB 백업·이전·보존 검사 실패. DB 자동 복원은 하지 않습니다.", timeout=1800)

    def journal(self, phase):
        self.phase = phase
        private_write(self.directory / "state.json", {"phase": phase, "project": self.project,
                      "volume": self.volume, "services_stopped": self.stopped,
                      "at": datetime.now(timezone.utc).isoformat()})
        print(phase, flush=True)

    def deploy(self):
        state_root = secure_dir(ROOT / ".local-deploy/redeploy")
        self.directory = Path(tempfile.mkdtemp(prefix="run-", dir=state_root))
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-") + self.directory.name[4:]
        updated = target_model(self.model, self.project, stamp)
        private_write(self.directory / "previous-compose.json", compose_text(self.model))
        private_write(self.directory / "compose.json", compose_text(updated))
        private_write(self.directory / "previous-containers.json", self.rows)
        shutil.copy2(ROOT / "deploy/redeploy_db.py", self.directory / "redeploy_db.py")
        self.compose("config", "--quiet", message="새 실행 구성 검사 실패. 서비스는 중지하지 않았습니다.")
        self.journal("새 이미지 빌드 중 — 기존 서비스는 계속 실행됩니다.")
        # Only the backend and web need building; workers share the same immutable tag.
        self.compose("build", "api", self.web, timeout=3600, message="이미지 빌드 실패. 기존 서비스는 중지하지 않았습니다.")
        self.idle()
        for role, row in zip(self.rows, self.inspect([item["Id"] for item in self.rows.values()])):
            require(row["State"]["Running"] and row["Id"] == self.rows[role]["Id"], "빌드 중 기존 컨테이너 상태가 바뀌었습니다.")
        self.stopped = True  # Even a partially successful stop requires explicit recovery.
        self.journal("서비스 중지 — 잠시 접속할 수 없습니다.")
        self.compose("stop", "--timeout", "120", self.web, "api", "worker", "model-tester", timeout=180)
        require(all(not row["State"]["Running"] for row in self.inspect([item["Id"] for item in self.rows.values()])),
                "기존 서비스가 모두 중지되지 않았습니다.")
        image = updated["services"]["api"]["image"]
        old_image = self.rows["api"]["Image"]
        self.journal("DB 백업 및 사본 마이그레이션 시험 중")
        self.helper(old_image, ["python", "-c", self.db_code, "backup", "/data/waf.db", "/backup/backup.db",
                    "/backup/baseline.json", str(os.getuid()), str(os.getgid())], live=True)
        self.helper(old_image, ["python", "-c", self.db_code, "idle", "/data/waf.db"], live=True)
        shutil.copy2(self.directory / "backup.db", self.directory / "rehearsal.db")
        self.helper(image, ["alembic", "upgrade", "head"], migration=True)
        self.helper(image, ["python", "-c", self.db_code, "verify", "/backup/rehearsal.db", "/backup/baseline.json"])
        self.journal("운영 DB 마이그레이션 및 기존 데이터 보존 검사 중")
        self.helper(image, ["alembic", "upgrade", "head"], live=True, migration=True)
        self.helper(image, ["python", "-c", self.db_code, "verify", "/data/waf.db", "/backup/baseline.json"], live=True)
        self.journal("API 시작 및 준비 상태 확인 중")
        self.compose("up", "-d", "--no-build", "--pull", "never", "--no-deps", "--force-recreate", "api")
        self.wait_api()
        self.journal("worker·웹 시작 및 상태 확인 중")
        self.compose("up", "-d", "--no-build", "--pull", "never", "--no-deps", "--force-recreate", "worker", "model-tester", self.web)
        self.verify_runtime(updated)
        self.stopped = False
        self.journal("재배포 완료")
        print("백업·실행 구성: " + str(self.directory))

    def wait_api(self):
        deadline = time.monotonic() + 150
        while time.monotonic() < deadline:
            ids = self.compose("ps", "-aq", "api").split()
            if ids:
                state = self.inspect(ids)[0]["State"]
                if state.get("Health", {}).get("Status") == "healthy":
                    return
                require(state.get("Status") not in ("exited", "dead"), "API가 종료됐습니다. DB는 자동 복원하지 않습니다.")
            time.sleep(3)
        raise DeployError("API 준비 상태 확인 시간이 초과됐습니다.")

    def verify_runtime(self, model):
        ids = self.compose("ps", "-aq").split()
        rows = {row["Config"]["Labels"]["com.docker.compose.service"]: row for row in self.inspect(ids)}
        require(validate(model, rows) == self.volume, "재배포 후 저장소가 다릅니다.")
        for row in rows.values():
            require(row["RestartCount"] == 0, "재배포 후 컨테이너가 재시작됐습니다.")
        self.run(["docker", "exec", rows[self.web]["Id"], "wget", "-q", "-O", "/dev/null", "http://127.0.0.1/"], "웹 응답 확인 실패")
        # Require heartbeats from the NEW containers, not recently stopped workers.
        heartbeat = """import sqlite3,sys
import json
db=sqlite3.connect('file:/data/waf.db?mode=ro',uri=True)
expected=json.loads(sys.argv[1])
for kind, hostname in expected:
    seen=db.execute("SELECT 1 FROM worker_heartbeats WHERE worker_kind IN (?, 'both') AND substr(worker_id,1,?)=? AND observed_at >= datetime('now','-45 seconds')", (kind,len(hostname)+1,hostname+':')).fetchone()
    if not seen: sys.exit(1)
"""
        expected = json.dumps([[kind, rows[role]["Config"]["Hostname"]]
                               for role, kind in (("worker", "analysis"), ("model-tester", "model_test"))])
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                self.run(["docker", "exec", rows["api"]["Id"], "python", "-c", heartbeat, expected], "worker heartbeat 확인 실패")
                latest = self.inspect(ids)
                require(all(row["State"]["Running"] and row["RestartCount"] == 0 for row in latest), "서비스가 종료 또는 재시작됐습니다.")
                return
            except DeployError:
                time.sleep(3)
        raise DeployError("worker 준비 상태를 확인하지 못했습니다.")


def main(argv=None):
    parser = argparse.ArgumentParser(description="기존 WAF 설정·DB를 보존하며 현재 소스를 재배포합니다. Git 작업이나 Gateway 변경은 하지 않습니다.")
    parser.add_argument("--check", action="store_true", help="읽기 전용 사전 확인만 수행")
    parser.add_argument("--yes", action="store_true", help="대상 확인 질문 생략")
    parser.add_argument("--project", help="기존 Compose 프로젝트 이름 (기본: 이 저장소의 API에서 탐색)")
    parser.add_argument("--file", "-f", action="append", help="기존 Compose 파일; 여러 파일은 순서대로 반복 지정")
    parser.add_argument("--env-file", help="기존 Compose env 파일 (값을 변경하거나 출력하지 않음)")
    args = parser.parse_args(argv)
    os.umask(0o077)
    task = Redeploy(args)
    lock = None
    try:
        if not args.check:
            directory = secure_dir(ROOT / ".local-deploy/redeploy")
            lock_path = directory / "operation.lock"
            require(not lock_path.is_symlink(), "잠금 파일이 symlink입니다.")
            lock = lock_path.open("a")
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise DeployError("다른 재배포 스크립트가 실행 중입니다.") from None
        task.discover()
        print("대상: " + task.project + " / 기존 Agent 모드: " + environment(task.rows["api"])["WAF_AGENT_MODE"])
        print("포트: " + json.dumps({name: ports(service) for name, service in task.model["services"].items() if service.get("ports")}))
        if args.check:
            print("사전 확인 통과. 빌드·서비스 중지·DB 변경은 하지 않았습니다.")
            return 0
        print("기존 키·모델·지침·스키마 설정을 유지합니다. 최신 DB migration을 적용하며 잠시 서비스가 중단됩니다.")
        print("기동 후 접수되는 분석은 기존 모델을 호출할 수 있습니다. 이미지 빌드에는 패키지 다운로드가 필요할 수 있습니다.")
        if not args.yes:
            require(sys.stdin.isatty(), "대화형 터미널에서 실행하거나 --yes를 지정하세요.")
            if input("계속하려면 프로젝트 이름을 입력하세요: ").strip() != task.project:
                print("취소했습니다. 서비스는 변경하지 않았습니다.")
                return 0
        task.deploy()
        return 0
    except (DeployError, OSError, ValueError, KeyError, KeyboardInterrupt, subprocess.SubprocessError) as error:
        print("중단: " + (str(error) if isinstance(error, DeployError) else "내부 검사 실패 또는 사용자 중단. 비밀 값 보호를 위해 세부 출력은 생략합니다."), file=sys.stderr)
        if task.directory:
            print("마지막 단계: " + task.phase + "\n기록: " + str(task.directory), file=sys.stderr)
        if task.stopped:
            print("일부 서비스가 중지되거나 새 버전으로 실행 중일 수 있습니다. 자동 롤백·DB 덮어쓰기는 하지 않았습니다. docs/Docker_Redeploy_Script.md의 복구 절차를 확인하세요.", file=sys.stderr)
        return 1
    finally:
        if lock:
            lock.close()


if __name__ == "__main__":
    sys.exit(main())
