"""Transactional, WAF-owned deployment on a reviewed existing team Gateway.

This is intentionally not a general-purpose Compose merger. It never runs the
team project's up/down scripts and never edits that project's files. Rollback
restores only the Gateway snapshot; databases and WAF migrations are not undone.
"""
from copy import deepcopy
from pathlib import Path
import re
import uuid
from urllib.parse import urlsplit

from .artifacts import gateway_bundle, runtime_env, secrets_for_install
from .common import (DeployError, Runner, canonical, compose_model, compose_text,
                     digest, json_output, operator_lock, private_json, private_write,
                     regular_file, secure_dir)
from .images import build_gateway, build_waf, source_fingerprint
from .resources import inspect_resources
from .team import discover_team, inspect_one, run_team_checks
from deploy.security.render_proxy import render, validate


PROJECT = "waf-ai-console-prod"
OWNER_LABEL = "io.waf.deploy.owner"
SERVICES = ("api", "waf-web", "worker", "model-tester")
INCOMPLETE = {"switching", "gateway_changed", "recovery_failed"}


def require(value, code):
    if not value:
        raise DeployError(code)


class Deployment:
    def __init__(self, settings, *, runner=None, build_mode="build", report=print):
        self.settings = settings
        self.root = Path(settings["root"])
        self.state_dir = self.root / ".local-deploy/automation"
        self.owner = digest(str(self.root.resolve()))[:24]
        self.runner = runner or Runner()
        self.build_mode = build_mode
        self.report = report
        self.touched_gateway_id = None

    def state(self, name):
        path = self.state_dir / (name + ".json")
        if not path.exists() and not path.is_symlink():
            return None
        for parent in (path.parent, *path.parent.parents):
            require(not parent.is_symlink(), "state_symlink_not_allowed")
        result = private_json(path)
        require(isinstance(result, dict), "deployment_state_invalid")
        return result

    def write(self, name, value):
        private_write(self.state_dir / (name + ".json"), value)

    def record(self, generation):
        require(isinstance(generation, str) and re.fullmatch(r"gen-[0-9a-f]{32}", generation), "generation_invalid")
        directory = self.state_dir / "generations" / generation
        result = private_json(directory / "record.json")
        require(result.get("owner") == self.owner and result.get("generation") == generation, "generation_owner_mismatch")
        for name, expected in result.get("artifacts", {}).items():
            require(isinstance(name, str) and not Path(name).is_absolute() and ".." not in Path(name).parts, "generation_path_invalid")
            path = directory / name
            require(all(not p.is_symlink() for p in (path, *path.parents)), "generation_symlink_not_allowed")
            require(digest(regular_file(path, private=True).read_bytes()) == expected, "generation_artifact_changed")
        return result

    def compose(self, record, file, *arguments, timeout=180):
        directory = self.state_dir / "generations" / record["generation"]
        project = PROJECT if file == "waf.json" else record["team"]["project"]
        return self.runner.run(["docker", "compose", "--project-directory", str(directory),
                                "--env-file", str(directory / "runtime.env"), "-p", project,
                                "-f", str(directory / file), *arguments],
                               code="compose_" + file.replace(".json", "") + "_failed", timeout=timeout)

    def identity(self):
        return {"owner": self.owner, "team_project": self.settings["team_project"],
                "team_dir": self.settings["team_dir"], "edge_name": self.settings["edge_name"],
                "edge_subnet": self.settings["edge_subnet"], "edge_range": self.settings["edge_range"],
                "peer": self.settings["peer"], "private_subnet": self.settings["private_subnet"],
                "web_ip": self.settings["web_ip"], "web_port": self.settings["web_port"]}

    def preflight(self):
        installation = self.state("installation")
        secret_state = self.state("secrets")
        current = self.state("current")
        journal = self.state("transaction")
        if installation:
            require(installation == self.identity(), "installation_identity_or_network_changed")
            require(secret_state is not None, "existing_encryption_state_missing")
            secrets_for_install(self.settings["values"], secret_state)
        else:
            require(not any((secret_state, current, journal)), "partial_installation_state_requires_recovery")
        require(not journal or journal.get("phase") not in INCOMPLETE, "interrupted_gateway_switch_run_rollback")
        active = self.record(current["generation"]) if current and current.get("active") else None
        team = discover_team(self.runner, self.settings,
                             managed_image=active["gateway_image"] if active else None,
                             managed_edge=self.settings["edge_name"] if active else None)
        run_team_checks(self.runner, team)
        resources = inspect_resources(self.runner, self.settings, self.owner, installation)
        self.team_health(team)
        source = source_fingerprint(self.root)
        if self.build_mode == "local":
            build_waf(self.runner, self.settings, "local", source, self.owner)
        return {"team": team, "resources": resources, "source": source, "active": active,
                "installation": installation, "secrets": secret_state}

    def curl(self, team, host, path):
        bind = team["host_contract"]["PLATFORM_GATEWAY_BIND_IP"]
        return self.runner.run(["curl", "--fail", "--silent", "--show-error", "--noproxy", "*",
                                "--proto", "=https", "--connect-timeout", "5", "--max-time", "15",
                                "--connect-to", f"{host}:443:{bind}:3030", f"https://{host}{path}"],
                               code="https_health_or_certificate_check_failed", timeout=20)

    def team_health(self, team):
        for host in (team["portal_host"], team["hub_host"]):
            self.curl(team, host, "/healthz")

    def waf_health(self, team):
        import json
        try:
            value = json.loads(self.curl(team, urlsplit(self.settings["origin"]).hostname, "/health/ready"))
        except ValueError:
            raise DeployError("waf_https_readiness_invalid") from None
        require(value.get("status") == "ok", "waf_https_not_ready")

    def same_deployment(self, data):
        active = data["active"]
        return bool(active and data["team"]["managed_runtime"]
                    and active["source"] == data["source"]
                    and active["settings"] == self.settings["fingerprint"]
                    and active["team"]["source_hashes"] == data["team"]["source_hashes"])

    def waf_running(self, record, containers):
        found = {}
        for item in containers:
            labels = item.get("Config", {}).get("Labels", {})
            if labels.get("com.docker.compose.oneoff", "False").lower() == "true":
                continue
            service = labels.get("com.docker.compose.service")
            if service not in SERVICES or service in found:
                return False
            found[service] = item
            expected = record["images"]["frontend" if service == "waf-web" else "backend"]
            if item.get("Image") != expected or labels.get("io.waf.deploy.generation") != record["generation"]:
                return False
            state = item.get("State", {})
            if not state.get("Running") or state.get("Health", {}).get("Status", "healthy") != "healthy":
                return False
        return set(found) == set(SERVICES)

    def check(self):
        data = self.preflight()
        self.report("사전 검사 통과: 팀 원본·TLS·네트워크·WAF 설정을 확인했습니다.")
        self.report("검사만 수행했습니다. 컨테이너·네트워크·키·DB를 생성하거나 변경하지 않았습니다.")
        self.report("Jupyter는 정적 격리 조건만 확인했습니다. 실제 통신 차단 시험과 외부 DNS/방화벽 경로는 운영 확인이 필요합니다.")
        if data["active"] and not data["team"]["managed_runtime"]:
            self.report("팀 Gateway 재배포로 WAF 연결이 빠져 있습니다. deploy로 다시 연결할 수 있습니다.")

    def status(self):
        journal = self.state("transaction")
        if journal and journal.get("phase") in INCOMPLETE:
            self.report("Gateway 전환이 완료되지 않았습니다. rollback으로 이전 Gateway를 복구하세요.")
            raise DeployError("interrupted_gateway_switch_run_rollback")
        data = self.preflight()
        if not data["active"]:
            self.report("활성 WAF 연결이 없습니다. 팀 Gateway는 정상입니다.")
            return
        if not data["team"]["managed_runtime"]:
            self.report("팀 Gateway는 정상이나 WAF 연결이 제거됐습니다. deploy로 재연결하세요.")
            raise DeployError("waf_gateway_attachment_missing")
        require(self.waf_running(data["active"], data["resources"]["containers"]), "waf_services_unhealthy_or_changed")
        self.waf_health(data["team"])
        self.report("정상: 기존 서비스와 WAF HTTPS 연결, WAF 서비스 실행 상태를 확인했습니다.")
        if not self.same_deployment(data):
            self.report("현재 코드 또는 env에 아직 배포하지 않은 변경이 있습니다.")

    def sources_unchanged(self, team, source=None):
        for path, expected in team["source_hashes"].items():
            require(digest(regular_file(path).read_bytes()) == expected, "team_source_changed_during_operation")
        if source:
            require(source_fingerprint(self.root) == source, "waf_source_changed_during_operation")

    def create_edge(self, resources):
        if self.settings["edge_name"] in resources["networks"]:
            return
        self.runner.run(["docker", "network", "create", "--driver", "bridge", "--internal",
                         "--label", OWNER_LABEL + "=" + self.owner,
                         "--subnet", self.settings["edge_subnet"], "--ip-range", self.settings["edge_range"],
                         self.settings["edge_name"]], code="waf_edge_creation_failed")

    def prepare(self, data):
        generation = "gen-" + uuid.uuid4().hex
        directory = secure_dir(self.state_dir / "generations" / generation)
        team = data["team"]
        base = deepcopy(data["active"]["base_team"] if team["managed_runtime"] else team)
        # source_hashes always describe the currently reviewed team files.
        base["source_hashes"] = team["source_hashes"]
        bundle = gateway_bundle(base, self.settings, "waf-gateway:" + generation)
        before = deepcopy(data["active"]["gateway_model"] if team["managed_runtime"] else bundle["rollback"])
        private_write(directory / "gateway-build/Dockerfile", bundle["dockerfile"])
        private_write(directory / "gateway-build/server.conf.template", bundle["template"])
        self.report("WAF 이미지와 Gateway 파생 이미지를 준비합니다. 기존 서비스는 계속 실행됩니다.")
        images = build_waf(self.runner, self.settings, self.build_mode, data["source"], self.owner)
        gateway_image = build_gateway(self.runner, directory, base["image_id"], self.owner, generation)
        for name in ("gateway", "check"):
            bundle[name]["services"]["gateway"]["image"] = gateway_image
        values = {**self.settings["values"], "WAF_BACKEND_IMAGE": images["backend"], "WAF_FRONTEND_IMAGE": images["frontend"]}
        private_write(directory / "runtime.env", runtime_env(values, data["secrets"]))
        for name, content in render(validate(values)).items():
            private_write(directory / "security" / name, content)
        private_write(directory / "nginx.production.conf", (self.root / "frontend/nginx.production.conf").read_text())
        model = compose_model(json_output(self.runner, ["docker", "compose", "--project-directory", str(self.root),
                              "--env-file", str(directory / "runtime.env"), "-p", PROJECT,
                              "-f", str(self.root / "docker-compose.production.yml"),
                              "-f", str(self.root / "deploy/security/docker-compose.gateway.yml"),
                              "config", "--format", "json"], code="waf_compose_invalid"))
        require(set(model["services"]) == set(SERVICES), "waf_compose_services_changed")
        for service, configuration in model["services"].items():
            configuration.pop("build", None)
            configuration["labels"] = {**configuration.get("labels", {}), OWNER_LABEL: self.owner,
                                        "io.waf.deploy.generation": generation}
            if service == "waf-web":
                for mount in configuration["volumes"]:
                    if mount["target"] == "/etc/nginx/waf-security":
                        mount["source"] = str(directory / "security")
                    elif mount["target"] == "/etc/nginx/conf.d/default.conf":
                        mount["source"] = str(directory / "nginx.production.conf")
            else:
                require(not configuration.get("ports"), "waf_api_ports_not_allowed")
        model["networks"]["waf-private"]["labels"] = {OWNER_LABEL: self.owner}
        model["volumes"]["waf-data"]["labels"] = {OWNER_LABEL: self.owner}
        files = {"waf.json": model, "gateway.json": bundle["gateway"], "candidate.json": bundle["check"], "rollback.json": before}
        for name, value in files.items():
            private_write(directory / name, compose_text(value))
        record = {"owner": self.owner, "generation": generation, "source": data["source"],
                  "settings": self.settings["fingerprint"], "team": team, "base_team": base,
                  "gateway_image": gateway_image, "images": images,
                  "gateway_model": bundle["gateway"], "before_model": before,
                  "previous": self.state("current") if team["managed_runtime"] else {"active": False},
                  "historical_current": self.state("current"), "artifacts": {}}
        for path in directory.rglob("*"):
            if path.is_file():
                record["artifacts"][str(path.relative_to(directory))] = digest(path.read_bytes())
        private_write(directory / "record.json", record)
        # Candidate has no published ports and no fixed IP, and retains original
        # TLS/CIDR mounts, user, capabilities and baked entrypoint.
        self.compose(record, "candidate.json", "run", "--rm", "--no-deps", "--pull", "never", "gateway", "nginx", "-t")
        self.sources_unchanged(team, data["source"])
        return record

    def up_gateway(self, record, file):
        before = self.compose(record, file, "ps", "--all", "-q", "gateway").split()
        expected = record["gateway_model"] if file == "gateway.json" else record["before_model"]
        try:
            self.compose(record, file, "up", "-d", "--no-deps", "--no-build", "--pull", "never",
                         "--force-recreate", "--wait", "--wait-timeout", "90", "gateway", timeout=180)
        finally:
            # Track the exact object created by this switch, including an up
            # that created the container but subsequently failed its wait.
            try:
                after = self.compose(record, file, "ps", "--all", "-q", "gateway").split()
                if len(after) == 1 and after[0] not in before:
                    live = inspect_one(self.runner, "container", after[0])
                    if live.get("Image") == expected["services"]["gateway"]["image"]:
                        self.touched_gateway_id = after[0]
            except Exception:
                pass

    def verify_gateway(self, record, model):
        from .runtime import assert_runtime_matches
        ids = self.compose(record, "gateway.json", "ps", "-q", "gateway").split()
        require(len(ids) == 1 and re.fullmatch(r"[0-9a-f]{64}", ids[0]), "gateway_after_switch_missing")
        assert_runtime_matches(self.runner, ids[0], model)
        runtime = inspect_one(self.runner, "container", ids[0])
        require(runtime.get("Image") == model["services"]["gateway"]["image"], "gateway_after_switch_image_mismatch")
        require(runtime.get("State", {}).get("Health", {}).get("Status") == "healthy", "gateway_after_switch_unhealthy")
        expected = {model["networks"][name]["name"]: attachment or {} for name, attachment in model["services"]["gateway"]["networks"].items()}
        live = runtime.get("NetworkSettings", {}).get("Networks", {})
        require(set(live) == set(expected), "gateway_after_switch_network_mismatch")
        for name, attachment in expected.items():
            if attachment.get("ipv4_address"):
                require(live[name].get("IPAddress") == attachment["ipv4_address"], "gateway_after_switch_ip_mismatch")
        self.team_health(record["team"])

    def restore(self, record):
        from .runtime import assert_runtime_matches
        # Refuse to overwrite a different operator's subsequent runtime/config.
        self.sources_unchanged(record["team"])
        ids = self.compose(record, "gateway.json", "ps", "--all", "-q", "gateway").split()
        require(len(ids) <= 1, "rollback_gateway_not_unique")
        if ids:
            live = inspect_one(self.runner, "container", ids[0])
            require(live.get("Image") in {record["gateway_image"], record["before_model"]["services"]["gateway"]["image"]}, "rollback_unexpected_gateway_requires_review")
            model = record["gateway_model"] if live["Image"] == record["gateway_image"] else record["before_model"]
            assert_runtime_matches(self.runner, ids[0], model, allow_stopped=True)
        self.up_gateway(record, "rollback.json")
        self.verify_gateway(record, record["before_model"])
        self.write("current", record["previous"] or {"active": False})
        self.write("transaction", {"phase": "rolled_back", "generation": record["generation"]})

    def recover(self, record):
        try:
            self.restore(record)
        except BaseException:
            self.write("transaction", {"phase": "recovery_failed", "generation": record["generation"]})
            # Stop only the exact object created by our switch/recovery. A
            # restored image that failed verification must not stay published.
            try:
                ids = self.compose(record, "gateway.json", "ps", "--all", "-q", "gateway").split()
                if len(ids) == 1 and ids[0] == self.touched_gateway_id:
                    self.runner.run(["docker", "container", "stop", "--time", "10", ids[0]], code="failed_gateway_stop_failed")
            except Exception:
                pass
            raise DeployError("gateway_recovery_failed_manual_review_required") from None

    def deploy(self):
        data = self.preflight()
        secure_dir(self.state_dir)
        with operator_lock(self.state_dir / "operator.lock"), operator_lock(data["team"]["operator_lock"], create=False):
            data = self.preflight()  # Discovery before taking a lock is never authority to write.
            if self.same_deployment(data) and self.waf_running(data["active"], data["resources"]["containers"]):
                self.waf_health(data["team"])
                self.report("이미 같은 코드와 설정으로 실행 중입니다. 재시작하지 않았습니다.")
                return
            if not data["installation"]:
                # Mark an empty installation before Docker can create any data.
                # A crash between these writes deliberately requires review.
                data["secrets"] = secrets_for_install(self.settings["values"])
                self.write("secrets", data["secrets"])
                self.write("installation", self.identity())
            record = self.prepare(data)
            self.write("transaction", {"phase": "prepared", "generation": record["generation"]})
            refreshed = inspect_resources(self.runner, self.settings, self.owner, self.state("installation"))
            for name in (self.settings["edge_name"], PROJECT + "_waf-private"):
                previous_network = data["resources"]["networks"].get(name)
                if previous_network:
                    require(refreshed["networks"].get(name, {}).get("Id") == previous_network.get("Id"), "waf_network_replaced_during_prepare")
            if data["resources"]["volume"]:
                require(refreshed["volume"] == data["resources"]["volume"], "waf_volume_replaced_during_prepare")
            self.create_edge(refreshed)
            # API startup applies WAF-only Alembic migrations. Stop existing WAF
            # workers before replacing API; team control plane is never targeted.
            self.compose(record, "waf.json", "stop", "worker", "model-tester", timeout=300)
            self.compose(record, "waf.json", "up", "-d", "--no-deps", "--no-build", "--pull", "never",
                         "--wait", "--wait-timeout", "120", "api", "waf-web", timeout=240)
            self.sources_unchanged(data["team"], data["source"])
            run_team_checks(self.runner, data["team"])
            self.report("WAF 준비 완료. Gateway만 교체합니다. 기존 HTTPS 연결이 잠시 끊길 수 있습니다.")
            self.write("transaction", {"phase": "switching", "generation": record["generation"]})
            try:
                self.up_gateway(record, "gateway.json")
                self.write("transaction", {"phase": "gateway_changed", "generation": record["generation"]})
                self.verify_gateway(record, record["gateway_model"])
                self.waf_health(data["team"])
                self.compose(record, "waf.json", "up", "-d", "--no-deps", "--no-build", "--pull", "never",
                             "--wait", "--wait-timeout", "60", "worker", "model-tester")
                self.sources_unchanged(data["team"], data["source"])
                self.write("current", {"active": True, "generation": record["generation"]})
                self.write("transaction", {"phase": "complete", "generation": record["generation"]})
            except BaseException:
                self.report("배포 확인에 실패했습니다. 직전 Gateway 설정으로 복구합니다.")
                self.recover(record)
                raise DeployError("deployment_failed_gateway_restored_waf_state_check_required") from None
        self.report("배포 완료: 기존 서비스와 WAF HTTPS 응답을 확인했습니다. 팀 원본 파일은 변경하지 않았습니다.")

    def rollback(self):
        from .team import local_docker
        local_docker(self.runner)
        installation = self.state("installation")
        require(installation == self.identity(), "rollback_installation_identity_mismatch")
        require(self.state("secrets") is not None, "existing_encryption_state_missing")
        journal, current = self.state("transaction"), self.state("current")
        target = journal if journal and journal.get("phase") in INCOMPLETE else current
        require(target and target.get("generation"), "no_gateway_rollback_available")
        record = self.record(target["generation"])
        with operator_lock(self.state_dir / "operator.lock"), operator_lock(record["team"]["operator_lock"], create=False):
            require(self.state("transaction") == journal and self.state("current") == current, "rollback_state_changed_retry")
            self.recover(record)
        self.report("직전 Gateway로 복구했습니다. WAF 컨테이너·분석 데이터·DB 마이그레이션은 되돌리거나 삭제하지 않았습니다.")
