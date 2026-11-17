"""Provision an isolated local SonarQube and scan a source-only snapshot.

No application code, WAF database, Git refs or system settings are modified.
Credentials and reports stay in .local-sonarqube, not in command arguments.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime
import fcntl
import importlib.util
from http.client import HTTPException
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import stat
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / ".local-sonarqube"
COMPOSE = Path(__file__).with_name("compose.yml")
PROJECT = "waf-ai-console-sonar"
PROJECT_KEY = "waf-ai-console-local"
DOCKER = ["docker", "--host", "unix:///var/run/docker.sock"]
IMAGE_TAGS = {
    "server": "sonarqube:community",
    "postgres": "postgres:17-alpine",
    "scanner": "sonarsource/sonar-scanner-cli:latest",
    "browser": "nginx:1.30.4-alpine",
}
IMAGE_REPOSITORIES = {name: tag.rsplit(":", 1)[0] for name, tag in IMAGE_TAGS.items()}
SENSITIVE: list[str] = []


class SonarError(RuntimeError):
    pass


def scrub(value: str) -> str:
    for secret in SENSITIVE:
        if secret:
            value = value.replace(secret, "[REDACTED]")
    return value


def private_directory(path: Path) -> None:
    if path.is_symlink():
        raise SonarError("Local state must not be a symbolic link.")
    path.mkdir(mode=0o700, parents=False, exist_ok=True)
    metadata = path.stat()
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid():
        raise SonarError("Local state must be a directory owned by the current user.")
    if stat.S_IMODE(metadata.st_mode) & 0o077:
        raise SonarError("Local state permissions must be 0700; refusing to change an existing directory.")


def write_new(path: Path, value: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(value)


def read_private(path: Path) -> str:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
        metadata = os.fstat(stream.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) & 0o077:
            raise SonarError("Private state file ownership/permissions are invalid.")
        value = stream.read(65537)
    if len(value) > 65536:
        raise SonarError("Local state file exceeds its limit.")
    return value.strip()


def clean_environment(extra: dict[str, str] | None = None) -> dict[str, str]:
    # In particular, do not inherit WAF_*, SONAR_*, COMPOSE_*, proxies or remote Docker contexts.
    environment = {key: os.environ[key] for key in ("PATH", "HOME", "LANG", "LC_ALL", "DOCKER_CONFIG") if key in os.environ}
    environment.update(extra or {})
    return environment


def command(args: list[str], *, extra: dict[str, str] | None = None, stream: bool = False, log: Path | None = None) -> str:
    with subprocess.Popen(args, cwd=ROOT, env=clean_environment(extra), stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace") as process:
        output = []
        logfile = None
        try:
            if log is not None:
                descriptor = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                logfile = os.fdopen(descriptor, "w", encoding="utf-8")
            assert process.stdout is not None
            for line in process.stdout:
                line = scrub(line)
                output.append(line)
                if logfile:
                    logfile.write(line)
                if stream:
                    print(line, end="", flush=True)
            code = process.wait()
        finally:
            if logfile:
                logfile.close()
        if code:
            if not stream:
                print("".join(output)[-3000:], file=sys.stderr)
            raise SonarError("Command failed; existing services and volumes were not removed.")
        return "".join(output).strip()


def valid_image(role: str, value: object) -> bool:
    if not isinstance(value, str) or not re.fullmatch(r"[a-z0-9./_-]+@sha256:[0-9a-f]{64}", value):
        return False
    repository = value.split("@", 1)[0].removeprefix("docker.io/").removeprefix("library/")
    return repository == IMAGE_REPOSITORIES.get(role)


def pinned_image(role: str, tag: str, offline: bool) -> str:
    if not offline:
        command(DOCKER + ["pull", tag], stream=True)
    digest = command(DOCKER + ["image", "inspect", tag, "--format", "{{index .RepoDigests 0}}"])
    if not valid_image(role, digest):
        raise SonarError("A digest from the expected official image repository is required.")
    return digest


def load_config(*, create: bool = False, port: int | None = None, offline: bool = False) -> dict:
    if port is not None and (type(port) is not int or not 1024 <= port <= 65535):
        raise SonarError("Invalid loopback port.")
    private_directory(STATE)
    target = STATE / "settings.json"
    if target.exists() or target.is_symlink():
        config = json.loads(read_private(target))
        if set(config) != {"version", "port", "images"} or config["version"] != 1:
            raise SonarError("Unsupported local installation settings.")
        if type(config["port"]) is not int or not 1024 <= config["port"] <= 65535:
            raise SonarError("Invalid saved loopback port.")
        if port is not None and port != config["port"]:
            raise SonarError("An existing installation keeps its port; do not replace its settings.")
        images = config["images"]
        if not isinstance(images, dict) or set(images) != set(IMAGE_TAGS) or any(
            not valid_image(role, value) for role, value in images.items()
        ):
            raise SonarError("Invalid saved image digests.")
        return config
    if not create:
        raise SonarError("No local installation. Run: bash scripts/sonarqube.sh up")
    existing = command(DOCKER + ["volume", "ls", "--filter", f"label=com.docker.compose.project={PROJECT}", "--format", "{{.Name}}"])
    if existing:
        raise SonarError("Existing SonarQube volumes lack local settings. Restore settings; do not initialize over them.")
    config = {"version": 1, "port": port or 19000,
              "images": {name: pinned_image(name, tag, offline) for name, tag in IMAGE_TAGS.items()}}
    generated_secret("postgres-password")
    generated_secret("admin-password")
    write_new(target, json.dumps(config, indent=2) + "\n")
    return config


def generated_secret(name: str) -> str:
    path = STATE / name
    if not path.exists() and not path.is_symlink():
        if (STATE / "settings.json").exists():
            raise SonarError("A saved installation is missing a credential file. Restore it; do not regenerate it.")
        write_new(path, secrets.token_urlsafe(36) + "aA1!\n")
    value = read_private(path)
    if not re.fullmatch(r"[A-Za-z0-9_!\-]{32,128}", value):
        raise SonarError("Invalid local credential file.")
    SENSITIVE.append(value)
    return value


def compose(config: dict, args: list[str], *, extra: dict[str, str] | None = None, log: Path | None = None) -> str:
    empty = STATE / "empty"
    private_directory(empty)
    environment = {
        "SONAR_SERVER_IMAGE": config["images"]["server"],
        "SONAR_POSTGRES_IMAGE": config["images"]["postgres"],
        "SONAR_SCANNER_IMAGE": config["images"]["scanner"],
        "SONAR_BROWSER_IMAGE": config["images"]["browser"],
        "SONAR_LOCAL_PORT": str(config["port"]),
        "SONAR_LOCAL_UID": str(os.getuid()), "SONAR_LOCAL_GID": str(os.getgid()),
        "SONAR_DB_PASSWORD": generated_secret("postgres-password"),
        "SONAR_SCAN_SOURCE": str(empty), "SONAR_SCAN_OUTPUT": str(empty),
    }
    environment.update(extra or {})
    return command(DOCKER + ["compose", "--env-file", "/dev/null", "--project-name", PROJECT,
                              "--file", str(COMPOSE)] + args, extra=environment, stream=True, log=log)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def api(config: dict, path: str, *, auth: str | tuple[str, str] | None = None,
        data: dict | None = None, query: dict | None = None) -> dict:
    if not re.fullmatch(r"/api/[a-z_]+/[a-z_]+", path):
        raise SonarError("Unexpected API endpoint.")
    url = f"http://127.0.0.1:{config['port']}{path}"
    if query:
        url += "?" + urlencode(query)
    headers = {"Accept": "application/json"}
    if isinstance(auth, tuple):
        headers["Authorization"] = "Basic " + base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
    elif auth:
        headers["Authorization"] = "Bearer " + auth
    request = Request(url, data=None if data is None else urlencode(data).encode(), headers=headers)
    try:
        with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=15) as response:
            body = response.read(16 * 1024 * 1024 + 1)
    except HTTPError as error:
        raise SonarError(f"SonarQube {path}: HTTP {error.code}") from None
    except (URLError, TimeoutError, ConnectionError, HTTPException):
        raise SonarError("Local SonarQube is not reachable yet.") from None
    if len(body) > 16 * 1024 * 1024:
        raise SonarError("SonarQube API response exceeds its limit.")
    if not body.strip():
        return {}
    try:
        return json.loads(body)
    except (ValueError, UnicodeError):
        raise SonarError(f"SonarQube {path}: response was not valid JSON.") from None


def check_platform() -> None:
    if sys.platform != "linux":
        raise SonarError("This local installation script targets Linux Docker Engine.")
    if not shutil.which("docker"):
        raise SonarError("Docker CLI is required.")
    command(DOCKER + ["version", "--format", "{{.Server.Version}}"])
    command(DOCKER + ["compose", "version", "--short"])
    maps = int(Path("/proc/sys/vm/max_map_count").read_text().strip())
    if maps < 524288:
        raise SonarError("Run in your terminal first: sudo sysctl -w vm.max_map_count=524288")
    if int(Path("/proc/sys/fs/file-max").read_text().strip()) < 131072:
        raise SonarError("Linux fs.file-max must be at least 131072; ask the host administrator.")
    if shutil.disk_usage(ROOT).free < 8 * 1024**3:
        raise SonarError("At least 8 GiB free disk space is required for local diagnostics.")
    print("Docker, Compose, Linux limits and free disk: OK", flush=True)


def wait_for_server(config: dict) -> None:
    deadline = time.monotonic() + 420
    last_status = None
    while time.monotonic() < deadline:
        try:
            status = api(config, "/api/system/status").get("status")
            if status == "UP":
                return
            if status in {"DB_MIGRATION_NEEDED", "DB_MIGRATION_RUNNING"}:
                raise SonarError("Existing SonarQube needs database migration; automatic upgrade is not allowed.")
            if status != last_status:
                print(f"SonarQube startup: {status}", flush=True)
                last_status = status
        except SonarError as error:
            if "migration" in str(error):
                raise
        time.sleep(2)
    raise SonarError("SonarQube startup timed out. Containers were preserved for inspection.")


def bootstrap(config: dict) -> str:
    password = generated_secret("admin-password")
    credentials = ("admin", password)
    if not api(config, "/api/authentication/validate", auth=credentials).get("valid"):
        if not api(config, "/api/authentication/validate", auth=("admin", "admin")).get("valid"):
            raise SonarError("Saved administrator credentials do not match. No password reset was attempted.")
        api(config, "/api/users/change_password", auth=("admin", "admin"),
            data={"login": "admin", "previousPassword": "admin", "password": password})
        if not api(config, "/api/authentication/validate", auth=credentials).get("valid"):
            raise SonarError("Administrator password rotation could not be verified.")
    try:
        api(config, "/api/components/show", auth=credentials, query={"component": PROJECT_KEY})
    except SonarError as error:
        if "HTTP 404" not in str(error):
            raise
        api(config, "/api/projects/create", auth=credentials,
            data={"project": PROJECT_KEY, "name": "WAF AI Console (local)", "visibility": "private"})
    api(config, "/api/projects/update_visibility", auth=credentials,
        data={"project": PROJECT_KEY, "visibility": "private"})
    token_path = STATE / "scan-token"
    if token_path.exists() or token_path.is_symlink():
        token = read_private(token_path)
        SENSITIVE.append(token)
        if not api(config, "/api/authentication/validate", auth=token).get("valid"):
            raise SonarError("Saved scan token is invalid. Replace it through the local SonarQube UI.")
        return token
    token = api(config, "/api/user_tokens/generate", auth=credentials,
                data={"name": "waf-local-scan-" + secrets.token_hex(6),
                      "type": "PROJECT_ANALYSIS_TOKEN", "projectKey": PROJECT_KEY}).get("token")
    if not isinstance(token, str) or not token:
        raise SonarError("SonarQube did not issue a project analysis token.")
    SENSITIVE.append(token)
    write_new(token_path, token + "\n")
    return token


def up(config: dict) -> None:
    current = command(DOCKER + ["ps", "--filter", f"label=com.docker.compose.project={PROJECT}",
                               "--filter", "label=com.docker.compose.service=browser", "--format", "{{.ID}}"])
    if not current:
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", config["port"]))
            except OSError:
                raise SonarError("The selected loopback port is already occupied; no existing service was changed.") from None
    compose(config, ["config", "--quiet"])
    compose(config, ["up", "-d", "--pull", "never", "db", "sonarqube", "browser"])
    wait_for_server(config)
    print("Securing local administrator and private project...", flush=True)
    bootstrap(config)
    print(f"SonarQube ready: http://127.0.0.1:{config['port']}", flush=True)
    print(f"Admin: admin; password file (not printed): {STATE / 'admin-password'}", flush=True)


def write_report(config: dict, run: Path, task_id: str) -> None:
    # A project-analysis token cannot read all administrative/report endpoints.
    auth = ("admin", generated_secret("admin-password"))
    deadline = time.monotonic() + 900
    while time.monotonic() < deadline:
        task = api(config, "/api/ce/task", auth=auth, query={"id": task_id})["task"]
        if task["status"] == "SUCCESS":
            break
        if task["status"] in {"FAILED", "CANCELED"}:
            raise SonarError("Server-side analysis did not complete; inspect the local SonarQube task.")
        time.sleep(3)
    else:
        raise SonarError("Server-side analysis is still pending; the submitted job was not canceled.")
    measures = api(config, "/api/measures/component", auth=auth, query={"component": PROJECT_KEY,
                   "metricKeys": "ncloc,files,bugs,vulnerabilities,code_smells,security_hotspots,duplicated_lines_density,coverage"})
    issues = api(config, "/api/issues/search", auth=auth,
                 query={"componentKeys": PROJECT_KEY, "ps": 100, "p": 1, "facets": "types,severities"})
    hotspots = api(config, "/api/hotspots/search", auth=auth, query={"project": PROJECT_KEY, "ps": 100, "p": 1})
    gate = api(config, "/api/qualitygates/project_status", auth=auth, query={"analysisId": task["analysisId"]})
    gate_evaluated = bool(gate.get("projectStatus", {}).get("conditions"))
    report = {"completed_at": datetime.now(ZoneInfo("Asia/Seoul")).isoformat(),
              "project_key": PROJECT_KEY, "task": task, "measures": measures, "quality_gate": gate,
              "issues_first_page": issues, "hotspots_first_page": hotspots,
              "coverage_measured": False, "tests_run_by_scanner": False,
              "quality_gate_conditions_evaluated": gate_evaluated}
    write_new(run / "report.json", json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    metrics = {item["metric"]: item.get("value") for item in measures["component"]["measures"]}
    dashboard = f"http://127.0.0.1:{config['port']}/dashboard?id={PROJECT_KEY}"
    lines = ["# WAF AI Console — local SonarQube scan", "", f"Dashboard: {dashboard}", "",
             "This is a source-only snapshot of the working tree, not a production security certification.",
             "Coverage was not measured or imported. The scanner did not execute application tests.",
             "Any Sonar coverage value reflects missing coverage input, not a measured test result.",
             "Quality Gate is reported only; WAF deployment and Git are not blocked or changed.", "",
             "| Metric | Value |", "| --- | --- |"]
    lines.extend(f"| {key} | {value} |" for key, value in sorted(metrics.items()) if key != "coverage")
    lines += ["", f"Quality Gate: {gate['projectStatus']['status']}", "",
              ("No Quality Gate conditions were evaluated; do not interpret OK as a security or release approval."
               if not gate_evaluated else "Quality Gate results do not replace review of all reported findings."), "",
              f"Issues: first {len(issues.get('issues', []))} of {issues.get('total', issues.get('paging', {}).get('total', '?'))}; full findings are in the dashboard.",
              "Security hotspots require human review and are not confirmed vulnerabilities.",
              "Frozen legacy engine copies may account for intentional duplication; do not merge them automatically.", "",
              "| Severity | Type | File | Line | Finding |", "| --- | --- | --- | --- | --- |"]
    for item in issues.get("issues", []):
        fields = [item.get("severity", ""), item.get("type", ""), item.get("component", ""),
                  str(item.get("line", "")), item.get("message", "")[:400]]
        lines.append("| " + " | ".join(str(value).replace("|", "\\|").replace("\n", " ") for value in fields) + " |")
    write_new(run / "report.md", "\n".join(lines) + "\n")
    print("Findings: " + json.dumps({key: value for key, value in metrics.items() if key != "coverage"}), flush=True)
    print(f"Report: {run / 'report.md'}", flush=True)


def scan(config: dict) -> None:
    if api(config, "/api/system/status").get("status") != "UP":
        raise SonarError("Start the local server before scanning.")
    token = bootstrap(config)
    runs = STATE / "runs"
    private_directory(runs)
    name = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(3)
    run = runs / name
    private_directory(run)
    source, output = run / "source", run / "scanner"
    private_directory(output)
    spec = importlib.util.spec_from_file_location("waf_sonar_snapshot", Path(__file__).with_name("snapshot.py"))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    manifest = module.build_snapshot(ROOT, source)
    write_new(run / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(f"Source-only snapshot: {manifest['source_file_count']} source / {manifest['test_file_count']} test files; {manifest['total_bytes']} bytes.", flush=True)
    compose(config, ["run", "--rm", "--no-deps", "scanner",
                    "-Dsonar.working.directory=/tmp/.scannerwork", "-Dsonar.scanner.metadataFilePath=/out/report-task.txt",
                    "-Dsonar.javascript.node.maxspace=1024", "-Dsonar.scanner.skipJreProvisioning=true"],
            extra={"SONAR_SCAN_SOURCE": str(source), "SONAR_SCAN_OUTPUT": str(output), "SONAR_SCAN_TOKEN": token},
            log=run / "scanner.log")
    write_report(config, run, read_task_id(run))


def read_task_id(run: Path) -> str:
    task_file = run / "scanner" / "report-task.txt"
    if task_file.is_symlink() or not task_file.is_file() or task_file.stat().st_size > 16384:
        raise SonarError("Scanner task metadata is missing or invalid.")
    task = dict(line.split("=", 1) for line in task_file.read_text().splitlines() if "=" in line)
    task_id = task.get("ceTaskId", "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", task_id):
        raise SonarError("Scanner did not provide a valid task ID.")
    return task_id


def resume_report(config: dict) -> None:
    runs = STATE / "runs"
    if runs.is_symlink() or not runs.is_dir():
        raise SonarError("No submitted local analysis is available.")
    candidates = sorted((path for path in runs.iterdir() if
                         re.fullmatch(r"\d{8}-\d{6}-[0-9a-f]{6}", path.name)), reverse=True)
    for run in candidates:
        if run.is_symlink() or not run.is_dir():
            raise SonarError("Local report directories must not be symbolic links.")
        if not (run / "scanner" / "report-task.txt").exists():
            continue
        if (run / "report.json").exists() and (run / "report.md").exists():
            print(f"Saved report: {run / 'report.md'}", flush=True)
            return
        task_id = read_task_id(run)
        target = run
        if (run / "report.json").exists() or (run / "report.md").exists():
            target = run / ("report-retry-" + secrets.token_hex(4))
            private_directory(target)
        write_report(config, target, task_id)
        return
    raise SonarError("No scanner task metadata was found; no analysis was resubmitted.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", nargs="?", default="all", choices=["all", "check", "up", "scan", "report", "status", "stop"])
    parser.add_argument("--check", action="store_true", help="Check installation prerequisites only")
    parser.add_argument("--port", type=int, help="New installation loopback port (default: 19000)")
    parser.add_argument("--offline", action="store_true", help="Use preloaded official images; do not pull")
    args = parser.parse_args(argv)
    if args.port is not None and not 1024 <= args.port <= 65535:
        parser.error("port must be between 1024 and 65535")
    try:
        private_directory(STATE)
        lock = os.open(STATE / "operation.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(lock, "w") as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise SonarError("Another local SonarQube operation is in progress.") from None
            action = "check" if args.check else args.action
            if action in {"all", "check", "up"}:
                check_platform()
            if action == "check":
                return 0
            config = load_config(create=action in {"all", "up"}, port=args.port, offline=args.offline)
            if action in {"all", "up"}:
                up(config)
            if action in {"all", "scan"}:
                scan(config)
            if action == "report":
                resume_report(config)
            if action == "status":
                compose(config, ["ps"])
                print("SonarQube: " + str(api(config, "/api/system/status").get("status")))
            if action == "stop":
                compose(config, ["stop", "browser", "sonarqube", "db"])
                print("Local diagnostics stopped; all data volumes were preserved.")
        return 0
    except (SonarError, OSError, ValueError) as error:
        message = str(error) if isinstance(error, SonarError) else f"Local configuration or file validation failed ({type(error).__name__})."
        print("Stopped: " + scrub(message), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
