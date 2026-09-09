#!/usr/bin/env python3
"""Read-only Compose contract check with synthetic settings, never a real env.

Runs only `docker compose config`. Does not build/start/stop containers, create
networks or volumes, read deployment secrets, or call models.
"""
import json
import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[2]
VALUES = {
    "WAF_BACKEND_IMAGE": "waf-api:synthetic-contract",
    "WAF_FRONTEND_IMAGE": "waf-web:synthetic-contract",
    "WAF_ADMIN_USERNAME": "synthetic-admin",
    "WAF_ADMIN_PASSWORD": "synthetic-password-not-a-secret",
    "WAF_SESSION_SECRET": "synthetic-session-not-a-secret",
    "WAF_DATA_ENCRYPTION_KEY": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
    "WAF_ENCRYPTION_KEY_VERSION": "synthetic-v1",
    "WAF_PUBLIC_ORIGIN": "https://waf.example.test",
    "WAF_SESSION_HTTPS_ONLY": "true",
    "WAF_PRIVATE_SUBNET": "172.30.251.0/24",
    "WAF_PRIVATE_WEB_IP": "172.30.251.10",
    "WAF_TRUSTED_PROXY_IP": "172.30.250.2",
    "WAF_EDGE_NETWORK": "waf-synthetic-edge",
    "WAF_WEB_PORT": "18080",
    "WAF_AGENT_MODE": "moduagent",
}


def compose(values, gateway=False):
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(("WAF_", "COMPOSE_")) and key != "FORWARDED_ALLOW_IPS"}
    environment.update(values)
    command = ["docker", "compose", "--env-file", "/dev/null", "-p", "waf-synthetic-contract",
               "-f", str(ROOT / "docker-compose.production.yml")]
    if gateway:
        command += ["-f", str(ROOT / "deploy/security/docker-compose.gateway.yml")]
    command += ["config", "--format", "json"]
    return subprocess.run(command, cwd=ROOT, env=environment, text=True, capture_output=True)


def check():
    for gateway in (False, True):
        result = compose(VALUES, gateway)
        assert result.returncode == 0, "synthetic Compose configuration failed"
        document = json.loads(result.stdout)
        services = document["services"]
        assert set(services) == {"api", "worker", "model-tester", "waf-web"}
        for name in ("api", "worker", "model-tester"):
            service = services[name]
            environment = service["environment"]
            assert not service.get("ports"), "backend host ports must not be published"
            assert set(service["networks"]) == {"waf-private"}
            assert environment["WAF_ENVIRONMENT"] == "production"
            assert environment["WAF_PUBLIC_ORIGIN"] == VALUES["WAF_PUBLIC_ORIGIN"]
            assert environment["WAF_SESSION_HTTPS_ONLY"] == "true"
            assert environment["FORWARDED_ALLOW_IPS"] == VALUES["WAF_PRIVATE_WEB_IP"]
            assert service["image"] == VALUES["WAF_BACKEND_IMAGE"]
        assert "--no-access-log" in services["api"]["command"][-1]
        assert "--proxy-headers" in services["api"]["command"][-1]
        assert services["worker"]["environment"]["WAF_WORKER_ROLE"] == "analysis"
        assert services["model-tester"]["environment"]["WAF_WORKER_ROLE"] == "model_test"
        web = services["waf-web"]
        assert len(web["ports"]) == 1 and web["ports"][0]["host_ip"] == "127.0.0.1"
        assert web["networks"]["waf-private"]["ipv4_address"] == VALUES["WAF_PRIVATE_WEB_IP"]
        expected_networks = {"waf-private", "waf-edge"} if gateway else {"waf-private"}
        assert set(web["networks"]) == expected_networks
        # Compose's normalized JSON may omit false bool fields.
        assert all(item["read_only"] and not item.get("bind", {}).get("create_host_path", False) for item in web["volumes"])
        assert document["networks"]["waf-private"]["ipam"]["config"][0]["subnet"] == VALUES["WAF_PRIVATE_SUBNET"]
        assert document["volumes"]["waf-data"]["name"] == "waf-ai-console-production-data"
        if gateway:
            assert document["networks"]["waf-edge"]["external"] is True
            assert document["networks"]["waf-edge"]["name"] == VALUES["WAF_EDGE_NETWORK"]
    for field in ("WAF_ADMIN_PASSWORD", "WAF_SESSION_SECRET", "WAF_DATA_ENCRYPTION_KEY",
                  "WAF_PUBLIC_ORIGIN", "WAF_PRIVATE_SUBNET", "WAF_PRIVATE_WEB_IP"):
        assert compose({**VALUES, field: ""}).returncode != 0, "missing required setting accepted"
    assert compose({**VALUES, "WAF_EDGE_NETWORK": ""}, True).returncode != 0
    print(json.dumps({"base_compose": "passed", "gateway_compose": "passed", "missing_fields": "rejected",
                      "backend_host_ports": 0, "service_mutations": 0, "real_env_read": False}))


if __name__ == "__main__":
    check()
