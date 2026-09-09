#!/usr/bin/env python3
"""Synthetic nginx two-hop regression in one network-none container, no LLM/DB.

Uses an already-installed nginx image, no pulls/builds, no host port publication.
The final API hop is a fixture echo server, not the production application.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("render_proxy", Path(__file__).with_name("render_proxy.py"))
policy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(policy)


def command(arguments, check=True):
    result = subprocess.run(arguments, capture_output=True, text=True, timeout=30)
    if check and result.returncode:
        raise RuntimeError("synthetic command failed: " + arguments[0] + "\n" + result.stderr[:3000])
    return result


def run(image):
    command(["docker", "image", "inspect", "--format", "{{.Id}}", image])
    container = "waf-proxy-fixture-" + uuid.uuid4().hex[:12]
    with tempfile.TemporaryDirectory(prefix="waf-proxy-fixture-") as directory:
        folder = Path(directory)
        settings = policy.validate({"WAF_PUBLIC_ORIGIN": "https://waf.example.test", "WAF_TRUSTED_PROXY_IP": "172.30.250.2",
                                    "WAF_PRIVATE_SUBNET": "172.30.251.0/24", "WAF_PRIVATE_WEB_IP": "172.30.251.10"})
        # Isolated loopback peers simulate gateway/web addresses without joining
        # any host, application, production, or external Docker network.
        settings["peer"] = "127.0.0.2"
        policy.write_policy(folder / "policy", settings)
        with (folder / "policy" / "proxy.conf").open("a") as stream:
            stream.write("proxy_bind 127.0.0.3;\n")
        production = (ROOT / "frontend/nginx.production.conf").read_text()
        production = production.replace("/etc/nginx/waf-security", "/test/policy").replace("listen 80 default_server", "listen 8081 default_server")
        production = production.replace("set $waf_api_target api:8000;", "set $waf_api_target 127.0.0.1:8082;").replace("/var/log/nginx/access.log", "/tmp/waf-minimal.log")
        snippet = (ROOT / "deploy/security/gateway-waf.server.conf.example").read_text()
        snippet = snippet.replace("listen 3030 ssl", "listen 8443 ssl").replace("waf.cyberailabs.team", "waf.example.test")
        snippet = snippet.replace("/run/platform-tls/tls.crt", "/test/tls.crt").replace("/run/platform-tls/tls.key", "/test/tls.key")
        snippet = snippet.replace("set $waf_web_target waf-web:80;", "set $waf_web_target 127.0.0.1:8081;")
        snippet = snippet.replace("proxy_pass http://$waf_web_target;", "proxy_pass http://$waf_web_target;\n    proxy_bind 127.0.0.2;")
        # This extra vhost has an absent WAF upstream. Its presence must not
        # prevent nginx startup or the other sites from serving requests.
        absent = snippet.replace("listen 8443 ssl", "listen 8444 ssl").replace(
            "set $waf_web_target 127.0.0.1:8081;", "set $waf_web_target absent-waf-fixture.invalid:80;")
        fixture = '''
map $uri $platform_ingress_allowed { default 1; /fixture-ingress-denied 0; }
geo $realip_remote_addr $fixture_api_trusted_peer { default 0; 127.0.0.3/32 1; }
map "$fixture_api_trusted_peer:$http_x_forwarded_proto" $fixture_api_scheme { default http; "1:https" https; }
server {
 listen 8082;
 set_real_ip_from 127.0.0.3;
 real_ip_header X-Forwarded-For;
 real_ip_recursive off;
 access_log off;
 error_log /dev/null crit;
 # Emulate forwarding trust, NOT an API network ACL. Uvicorn ignores forwarding
 # from other peers; FORWARDED_ALLOW_IPS does not reject their TCP connections.
 location / { return 200 "$fixture_api_scheme|$remote_addr|$http_host|$realip_remote_addr"; }
}
server {
 listen 8083;
 access_log off;
 error_log /dev/null crit;
 location / {
  proxy_bind 127.0.0.2;
  proxy_set_header Host waf.example.test;
  proxy_set_header X-Forwarded-For $http_x_fixture_ip;
  proxy_set_header X-Forwarded-Proto $http_x_fixture_proto;
  proxy_pass http://127.0.0.1:8081;
 }
}
'''
        (folder / "nginx.conf").write_text("pid /tmp/nginx-fixture.pid;\nerror_log /dev/null crit;\nevents {}\nhttp {\n" + fixture + production + snippet + absent + "\n}\n")
        command(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", "/CN=waf.example.test",
                 "-keyout", str(folder / "tls.key"), "-out", str(folder / "tls.crt")])
        os.chmod(folder / "tls.key", 0o600)
        base = ["docker", "run", "--pull", "never", "--network", "none", "--add-host", "waf.example.test:127.0.0.1",
                "--add-host", "other.example.test:127.0.0.1",
                "--mount", f"type=bind,source={folder},target=/test,readonly", "--entrypoint", "nginx"]
        command(base + ["--rm", image, "-t", "-c", "/test/nginx.conf"])
        started = False
        try:
            command(base + ["--name", container, "-d", image, "-c", "/test/nginx.conf", "-g", "daemon off;"])
            started = True

            def fetch(path="/api/v1/fixture", *, via="gateway", headers=(), post=False, tls_host="waf.example.test"):
                if via == "gateway":
                    url = "https://" + tls_host + ":8443" + path
                else:
                    url = f"http://127.0.0.1:{dict(web=8081, api=8082, malformed=8083)[via]}" + path
                arguments = ["docker", "exec", container, "wget", "-T", "5", "-S", "-O", "-", "--no-check-certificate"]
                if not any(header.lower().startswith("host:") for header in headers):
                    arguments += ["--header", "Host: waf.example.test"]
                for header in headers:
                    arguments += ["--header", header]
                if post:
                    arguments += ["--post-data", "SYNTHETIC_BODY_DO_NOT_LOG"]
                result = command(arguments + [url], check=False)
                statuses = re.findall(r"HTTP/\d(?:\.\d)? (\d{3})", result.stderr)
                return (int(statuses[-1]) if statuses else None), result.stdout, result

            for _ in range(40):
                status, _, _ = fetch()
                if status == 200:
                    break
                time.sleep(0.1)
            assert status == 200, "fixture startup did not succeed"
            status, body, _ = fetch(headers=("X-Forwarded-For: 203.0.113.200", "X-Forwarded-Proto: http", "Forwarded: for=203.0.113.200;proto=http"))
            assert status == 200 and body == "https|127.0.0.1|waf.example.test|127.0.0.3", "gateway must overwrite spoofed forwarding headers and preserve both trusted hops"
            assert fetch(via="web", headers=("X-Forwarded-For: 172.30.250.2", "X-Forwarded-Proto: https"))[0] == 403, "direct untrusted frontend peer must be rejected"
            direct_status, direct_body, _ = fetch(via="api", headers=("X-Forwarded-For: 203.0.113.200", "X-Forwarded-Proto: https"))
            assert direct_status == 200 and direct_body == "http|127.0.0.1|waf.example.test|127.0.0.1", "fixture API must ignore forwarding from untrusted peers, not pretend to enforce a TCP ACL"
            assert fetch(via="malformed", headers=("X-Fixture-IP: 127.0.0.1", "X-Fixture-Proto: http"))[0] == 400, "trusted peer cannot submit HTTP protocol"
            for address in ("", "127.0.0.1,203.0.113.1", "999.999.999.999", "not-an-ip", "255.255.255.255", "127.0.0.1:1234", "[2001:db8::1]:443"):
                assert fetch(via="malformed", headers=("X-Fixture-IP: " + address, "X-Fixture-Proto: https"))[0] == 400, "malformed forwarded IP must be rejected"
            ipv6_status, ipv6_body, _ = fetch(via="malformed", headers=("X-Fixture-IP: 2001:db8::1", "X-Fixture-Proto: https"))
            assert ipv6_status == 200 and ipv6_body == "https|2001:db8::1|waf.example.test|127.0.0.3", "canonical IPv6 client must survive the IPv4 proxy hops"
            assert fetch("/fixture-ingress-denied")[0] is None, "existing gateway ingress ACL must remain effective"
            assert fetch(headers=("Host: other.example.test",))[0] == 421, "wrong public host must fail"
            assert fetch(tls_host="other.example.test")[0] == 421, "TLS SNI and HTTP Host must agree"
            codes = [fetch("/api/v1/auth/login", post=True)[0] for _ in range(9)]
            assert 200 in codes and 429 in codes, "login requests must reach per-client rate limit"
            for path in ("/api/v1/auth/login/", "/api/v1/auth/%6cogin", "/api/v1//auth/login", "/api/v1/auth/login?unused=1"):
                assert fetch(path, post=True, headers=("X-Forwarded-For: 203.0.113.200",))[0] == 429, "login path variations and forged IP must not bypass the exhausted client bucket"
            assert fetch("/api/v1/auth/login", via="malformed", post=True, headers=("X-Fixture-IP: 203.0.113.22", "X-Fixture-Proto: https"))[0] == 200, "a different canonical client needs its own login bucket"
            assert fetch("/api/v1/fixture?SYNTHETIC_QUERY_DO_NOT_LOG", headers=("Cookie: session=SYNTHETIC_COOKIE_DO_NOT_LOG", "X-API-Key: SYNTHETIC_KEY_DO_NOT_LOG"), post=True)[0] == 200
            logs = command(["docker", "exec", container, "cat", "/tmp/waf-minimal.log"]).stdout
            assert "status=" in logs and "client=" in logs
            assert "SYNTHETIC_" not in logs and "/api/" not in logs, "sensitive query/body/cookie/key must not reach access logs"
            print(json.dumps({"nginx_syntax": "passed", "two_hop_https_client_ip": "passed", "forged_headers": "rejected",
                              "untrusted_frontend_peer": "rejected", "untrusted_api_forwarding": "ignored_by_fixture", "malformed_forwarding": "rejected", "gateway_ingress_acl": "preserved",
                              "canonical_ipv6": "passed", "host_sni_match": "required", "absent_upstream_startup": "passed", "login_alias_and_forged_ip_bypass": "rejected",
                              "login_rate_limit": 429, "sensitive_log_values": 0, "published_ports": 0, "network": "none",
                              "api": "fixture_only", "llm_calls": 0}))
        finally:
            if started:
                # Only the exact random fixture container created in this run.
                command(["docker", "rm", "-f", container], check=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default="nginx:1.30.4-alpine")
    run(parser.parse_args().image)
