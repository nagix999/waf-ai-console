#!/usr/bin/env python3
"""Render/verify local nginx policy without reading or printing application secrets.

Only the named non-secret settings are consumed. Company/VPN admission remains
the existing firewall and gateway policy, not a duplicated Docker CIDR ACL.
"""
import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit

FIELDS = ("WAF_PUBLIC_ORIGIN", "WAF_TRUSTED_PROXY_IP", "WAF_PRIVATE_SUBNET", "WAF_PRIVATE_WEB_IP")
PRIVATE = tuple(ipaddress.ip_network(value) for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
VERSION = 1


class PolicyError(ValueError):
    pass


def read_settings(path, environ=None):
    values = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if not separator or key not in FIELDS:
            continue
        if key in values:
            raise PolicyError("duplicate setting: " + key)
        value = value.strip()
        if value[:1] in {"'", '"'}:
            if len(value) < 2 or value[-1] != value[0]:
                raise PolicyError("invalid quoting: " + key)
            value = value[1:-1]
        values[key] = value
    # Match Compose's environment-over-env-file precedence for these fields.
    for key in FIELDS:
        if key in (os.environ if environ is None else environ):
            values[key] = (os.environ if environ is None else environ)[key]
    return values


def private_ip(value, field):
    try:
        address = ipaddress.IPv4Address(value)
    except (ValueError, TypeError):
        raise PolicyError("invalid private IPv4 setting: " + field) from None
    if not any(address in subnet for subnet in PRIVATE):
        raise PolicyError("private IPv4 required: " + field)
    return address


def validate(values):
    for key in FIELDS:
        value = values.get(key)
        if not isinstance(value, str) or not value or value != value.strip() or any(character.isspace() for character in value):
            raise PolicyError("missing or invalid setting: " + key)
    origin = values["WAF_PUBLIC_ORIGIN"]
    try:
        parsed = urlsplit(origin)
        hostname = parsed.hostname or ""
        port = parsed.port
        valid_host = bool(re.fullmatch(r"(?=.{1,253}$)[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", hostname))
        valid_labels = all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in hostname.split("."))
        if (parsed.scheme != "https" or parsed.path or parsed.query or parsed.fragment or "?" in origin or "#" in origin
                or parsed.username is not None or parsed.password is not None or not valid_host or not valid_labels
                or (port is not None and not 1 <= port <= 65535)):
            raise ValueError()
        authority = hostname + (f":{port}" if port not in {None, 443} else "")
        if origin != "https://" + authority:
            raise ValueError()
    except ValueError:
        raise PolicyError("canonical HTTPS origin required: WAF_PUBLIC_ORIGIN") from None
    peer = private_ip(values["WAF_TRUSTED_PROXY_IP"], "WAF_TRUSTED_PROXY_IP")
    web = private_ip(values["WAF_PRIVATE_WEB_IP"], "WAF_PRIVATE_WEB_IP")
    try:
        subnet = ipaddress.IPv4Network(values["WAF_PRIVATE_SUBNET"], strict=True)
    except ValueError:
        raise PolicyError("invalid network: WAF_PRIVATE_SUBNET") from None
    if not any(subnet.subnet_of(parent) for parent in PRIVATE) or not 16 <= subnet.prefixlen <= 28:
        raise PolicyError("private IPv4 network /16 through /28 required: WAF_PRIVATE_SUBNET")
    # Docker normally allocates the first usable address to its bridge gateway.
    if web not in subnet or int(web) <= int(subnet.network_address) + 1 or web == subnet.broadcast_address or web == peer:
        raise PolicyError("WAF_PRIVATE_WEB_IP must be a distinct usable address in WAF_PRIVATE_SUBNET")
    return {"origin": origin, "authority": authority, "hostname": hostname, "port": port or 443,
            "peer": str(peer), "web": str(web), "subnet": str(subnet)}


def render(settings):
    authority, peer = settings["authority"], settings["peer"]
    http = f'''# Generated local policy. Do not edit; render and --check before deployment.
set_real_ip_from {peer};
real_ip_header X-Forwarded-For;
real_ip_recursive off;
geo $realip_remote_addr $waf_approved_peer {{
  default 0;
  {peer}/32 1;
}}
# geo maps invalid/missing addresses to 255.255.255.255, which nginx does not
# accept as a /32 configuration entry. Reject its reserved /24 instead.
geo $http_x_forwarded_for $waf_valid_forwarded_ip {{
  default 1;
  255.255.255.0/24 0;
}}
map $http_x_forwarded_proto $waf_https {{
  default 0;
  https 1;
}}
map $http_host $waf_expected_host {{
  default 0;
  {authority} 1;
}}
limit_req_zone $binary_remote_addr zone=waf_login:10m rate=5r/m;
log_format waf_minimal '$time_iso8601 client=$remote_addr method=$request_method status=$status bytes=$body_bytes_sent';
'''
    server = '''if ($waf_approved_peer = 0) { return 403; }
if ($waf_expected_host = 0) { return 421; }
if ($waf_https = 0) { return 400; }
if ($waf_valid_forwarded_ip = 0) { return 400; }
# A correctly configured gateway sends canonical $remote_addr, not an XFF list.
# A list, port suffix, or noncanonical spelling must not reach the API.
if ($http_x_forwarded_for != $remote_addr) { return 400; }
'''
    proxy = f'''proxy_read_timeout 75s;
proxy_set_header Host {authority};
proxy_set_header X-Forwarded-Host {authority};
proxy_set_header X-Forwarded-Proto https;
proxy_set_header X-Forwarded-Port {settings["port"]};
proxy_set_header X-Real-IP $remote_addr;
proxy_set_header X-Forwarded-For $remote_addr;
proxy_set_header Forwarded "";
proxy_set_header X-Original-URL "";
'''
    return {"http.conf": http, "server.conf": server, "proxy.conf": proxy}


def expected_manifest(settings, contents):
    digest = lambda value: hashlib.sha256(value.encode("utf-8")).hexdigest()
    return {"version": VERSION, "settings_hash": digest(json.dumps(settings, sort_keys=True)),
            "files": {name: digest(value) for name, value in contents.items()}}


def ensure_directory(path):
    if path.is_symlink():
        raise PolicyError("output directory must not be a symlink")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.stat().st_mode & 0o077:
        raise PolicyError("output directory must have mode 0700")


def write_policy(path, settings):
    ensure_directory(path)
    contents = render(settings)
    contents["manifest.json"] = json.dumps(expected_manifest(settings, contents), sort_keys=True, indent=2) + "\n"
    for name, value in contents.items():
        target = path / name
        if target.is_symlink():
            raise PolicyError("output file must not be a symlink")
        descriptor, temporary = tempfile.mkstemp(prefix=".render-", dir=path)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(value)
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def check_policy(path, settings):
    if not path.is_dir() or path.is_symlink() or path.stat().st_mode & 0o077:
        raise PolicyError("private rendered policy directory required")
    contents = render(settings)
    expected = expected_manifest(settings, contents)
    for name, value in {**contents, "manifest.json": json.dumps(expected, sort_keys=True, indent=2) + "\n"}.items():
        target = path / name
        if not target.is_file() or target.is_symlink() or target.stat().st_mode & 0o077 or target.read_text(encoding="utf-8") != value:
            raise PolicyError("rendered policy missing, changed, or stale; run renderer again")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=".env.production")
    parser.add_argument("--output", default=".local-deploy/production-security")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        settings = validate(read_settings(args.env_file))
        output = Path(args.output)
        if args.check:
            check_policy(output, settings)
        else:
            write_policy(output, settings)
            check_policy(output, settings)
    except (PolicyError, OSError, UnicodeError):
        # Environment files may contain other secrets; never echo their content
        # or exception payloads. PolicyError messages only identify field names.
        import sys
        error = sys.exc_info()[1]
        message = str(error) if isinstance(error, PolicyError) else "cannot read/write private policy files"
        parser.exit(1, message + "\n")
    print("Proxy policy verified; secret values were not printed.")


if __name__ == "__main__":
    main()
