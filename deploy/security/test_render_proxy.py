"""Pure standard-library tests. All network values are private test fixtures."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

MODULE = Path(__file__).with_name("render_proxy.py")
SPEC = importlib.util.spec_from_file_location("render_proxy", MODULE)
policy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(policy)


def sample(**changes):
    return {"WAF_PUBLIC_ORIGIN": "https://waf.example.test", "WAF_TRUSTED_PROXY_IP": "172.30.250.2",
            "WAF_PRIVATE_SUBNET": "172.30.251.0/24", "WAF_PRIVATE_WEB_IP": "172.30.251.10", **changes}


class PolicyTests(unittest.TestCase):
    def test_exact_peer_and_private_web_hops(self):
        settings = policy.validate(sample())
        output = policy.render(settings)
        self.assertIn("set_real_ip_from 172.30.250.2;", output["http.conf"])
        self.assertIn("geo $realip_remote_addr $waf_approved_peer", output["http.conf"])
        self.assertIn("172.30.250.2/32 1;", output["http.conf"])
        self.assertIn("real_ip_recursive off;", output["http.conf"])
        self.assertIn("if ($waf_approved_peer = 0) { return 403; }", output["server.conf"])
        self.assertIn("if ($waf_https = 0) { return 400; }", output["server.conf"])
        self.assertIn("geo $http_x_forwarded_for $waf_valid_forwarded_ip", output["http.conf"])
        self.assertIn("255.255.255.0/24 0;", output["http.conf"])
        self.assertIn("if ($waf_valid_forwarded_ip = 0) { return 400; }", output["server.conf"])
        self.assertIn("if ($http_x_forwarded_for != $remote_addr) { return 400; }", output["server.conf"])
        self.assertIn("proxy_set_header X-Forwarded-For $remote_addr;", output["proxy.conf"])
        self.assertIn("proxy_set_header X-Forwarded-Proto https;", output["proxy.conf"])

    def test_no_company_network_allowlist_is_invented(self):
        result = "\n".join(policy.render(policy.validate(sample())).values())
        self.assertNotIn("allow ", result)
        self.assertNotIn("172.30.251.0/24", result)

    def test_empty_settings_fail_closed(self):
        for key in policy.FIELDS:
            with self.subTest(key=key), self.assertRaises(policy.PolicyError):
                policy.validate(sample(**{key: ""}))

    def test_https_origin_injection_and_ambiguity_rejected(self):
        for value in ("http://waf.example.test", "https://waf.example.test/", "https://u:p@waf.example.test",
                      "https://waf.example.test?", "https://waf.example.test#", "https://waf.example.test:443",
                      "https://WAF.example.test", "https://waf.example.test;", "https://waf.example.test\ninclude", "https://*.example.test"):
            with self.subTest(value=value), self.assertRaises(policy.PolicyError):
                policy.validate(sample(WAF_PUBLIC_ORIGIN=value))

    def test_explicit_nonstandard_https_port_preserved(self):
        settings = policy.validate(sample(WAF_PUBLIC_ORIGIN="https://waf.example.test:8443"))
        self.assertIn("proxy_set_header Host waf.example.test:8443;", policy.render(settings)["proxy.conf"])
        self.assertIn("proxy_set_header X-Forwarded-Port 8443;", policy.render(settings)["proxy.conf"])

    def test_unsafe_or_ambiguous_addresses_rejected(self):
        for key, values in {
            "WAF_TRUSTED_PROXY_IP": ("127.0.0.1", "8.8.8.8", "169.254.1.2", "gateway", "172.30.250.0/24", "172.30.250.2;", "172.30.250.2,172.30.250.3"),
            "WAF_PRIVATE_SUBNET": ("0.0.0.0/0", "8.8.8.0/24", "172.30.251.1/24", "172.30.251.0/30"),
            "WAF_PRIVATE_WEB_IP": ("172.30.251.0", "172.30.251.1", "172.30.251.255", "172.30.250.2", "172.30.252.10"),
        }.items():
            for value in values:
                with self.subTest(key=key, value=value), self.assertRaises(policy.PolicyError):
                    policy.validate(sample(**{key: value}))

    def test_private_host_proxy_peer_supported(self):
        settings = policy.validate(sample(WAF_TRUSTED_PROXY_IP="172.30.251.1"))
        self.assertEqual(settings["peer"], "172.30.251.1")

    def test_secret_fields_never_consumed_and_environment_precedence_matches_compose(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "settings.env"
            source.write_text("\n".join(f"{key}={value}" for key, value in sample().items()) + "\nWAF_ADMIN_PASSWORD=DO_NOT_READ\nWAF_SESSION_SECRET=DO_NOT_READ\n")
            values = policy.read_settings(source, {"WAF_TRUSTED_PROXY_IP": "172.30.250.3"})
            self.assertEqual(set(values), set(policy.FIELDS))
            self.assertEqual(values["WAF_TRUSTED_PROXY_IP"], "172.30.250.3")

    def test_duplicate_and_invalid_quote_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "settings.env"
            for content in ("WAF_PUBLIC_ORIGIN=x\nWAF_PUBLIC_ORIGIN=y", 'WAF_PUBLIC_ORIGIN="missing'):
                source.write_text(content)
                with self.assertRaises(policy.PolicyError):
                    policy.read_settings(source, {})

    def test_generated_files_are_private_and_verified(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "policy"
            settings = policy.validate(sample())
            policy.write_policy(target, settings)
            policy.check_policy(target, settings)
            self.assertEqual(target.stat().st_mode & 0o777, 0o700)
            for file in target.iterdir():
                self.assertEqual(file.stat().st_mode & 0o777, 0o600)
            manifest = json.loads((target / "manifest.json").read_text())
            self.assertEqual(set(manifest["files"]), {"http.conf", "server.conf", "proxy.conf"})

    def test_modified_or_stale_policy_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "policy"
            settings = policy.validate(sample())
            policy.write_policy(target, settings)
            with self.assertRaises(policy.PolicyError):
                policy.check_policy(target, policy.validate(sample(WAF_TRUSTED_PROXY_IP="172.30.250.3")))
            (target / "server.conf").write_text("# removed controls")
            with self.assertRaises(policy.PolicyError):
                policy.check_policy(target, settings)

    def test_symlink_and_world_readable_policy_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "policy"
            settings = policy.validate(sample())
            policy.write_policy(target, settings)
            (target / "http.conf").chmod(0o644)
            with self.assertRaises(policy.PolicyError):
                policy.check_policy(target, settings)
            alias = Path(temporary) / "alias"
            alias.symlink_to(target)
            with self.assertRaises(policy.PolicyError):
                policy.write_policy(alias, settings)

    def test_minimal_log_excludes_sensitive_fields(self):
        configuration = policy.render(policy.validate(sample()))["http.conf"]
        log = configuration[configuration.index("log_format"):]
        for value in ("$request'", "$request_uri", "$uri", "$args", "$request_body", "$http_cookie", "$http_authorization", "$http_x_api_key"):
            self.assertNotIn(value, log)
        production = MODULE.parents[2] / "frontend" / "nginx.production.conf"
        text = production.read_text()
        self.assertIn("error_log /dev/null crit;", text)
        self.assertIn("limit_req_status 429;", text)

    def test_login_rate_limit_covers_redirecting_slash_and_uses_restored_ip(self):
        production = (MODULE.parents[2] / "frontend" / "nginx.production.conf").read_text()
        self.assertIn("location ~ ^/api/v1/auth/login/?$", production)
        self.assertIn("limit_req zone=waf_login burst=5 nodelay;", production)
        self.assertIn("limit_req_zone $binary_remote_addr zone=waf_login:10m rate=5r/m;",
                      policy.render(policy.validate(sample()))["http.conf"])

    def test_gateway_keeps_existing_ingress_policy_and_uses_runtime_dns(self):
        gateway = MODULE.with_name("gateway-waf.server.conf.example").read_text()
        self.assertIn("if ($platform_ingress_allowed = 0) { return 444; }", gateway)
        self.assertIn("resolver 127.0.0.11 valid=10s ipv6=off;", gateway)
        self.assertIn("set $waf_web_target waf-web:80;", gateway)
        self.assertIn("proxy_pass http://$waf_web_target;", gateway)
        self.assertIn("if ($ssl_server_name != $host) { return 421; }", gateway)
        self.assertIn("proxy_set_header X-Forwarded-For $remote_addr;", gateway)
        self.assertNotIn("$proxy_add_x_forwarded_for", gateway)
        self.assertNotIn("allow 10.", gateway)


if __name__ == "__main__":
    unittest.main()
